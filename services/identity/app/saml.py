"""SAML 2.0 Service Provider authentication."""

from __future__ import annotations

import os
from typing import Any

# Common SAML attribute names IdPs actually send, normalized onto the
# claim keys federation.py already expects from OIDC userinfo.
_ATTRIBUTE_ALIASES = {
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress": "email",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name": "name",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/groups": "groups",
    "email": "email",
    "mail": "email",
    "name": "name",
    "displayName": "name",
    "groups": "groups",
    "Groups": "groups",
    "memberOf": "groups",
}


def saml_enabled() -> bool:
    return bool(os.environ.get("HOLON_SAML_IDP_METADATA_URL") or os.environ.get("HOLON_SAML_IDP_METADATA_XML"))


def _sp_private_key() -> str:
    """Resolve SP private key from secret provider or environment."""
    raw = os.environ.get("HOLON_SAML_SP_PRIVATE_KEY", "")
    if raw.startswith(("env:", "vault:", "k8s:", "aws:")):
        from holon_common.secrets import get_secret

        return get_secret(raw)
    return raw


def _idp_settings() -> dict[str, Any]:
    from onelogin.saml2.idp_metadata_parser import OneLogin_Saml2_IdPMetadataParser

    xml = os.environ.get("HOLON_SAML_IDP_METADATA_XML")
    if xml:
        parsed = OneLogin_Saml2_IdPMetadataParser.parse(xml)
    else:
        parsed = OneLogin_Saml2_IdPMetadataParser.parse_remote(os.environ["HOLON_SAML_IDP_METADATA_URL"])
    return parsed["idp"]


def _acs_url() -> str:
    return os.environ.get("HOLON_SAML_SP_ACS_URL", "http://localhost:8001/saml/acs")


def _sp_settings() -> dict[str, Any]:
    return {
        "entityId": os.environ.get("HOLON_SAML_SP_ENTITY_ID", "http://localhost:8001/saml/metadata"),
        "assertionConsumerService": {
            "url": _acs_url(),
            "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
        },
        "NameIDFormat": "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent",
        "x509cert": os.environ.get("HOLON_SAML_SP_CERT", ""),
        "privateKey": _sp_private_key(),
    }


def _settings_dict() -> dict[str, Any]:
    """Build complete SAML settings dictionary for IdP interaction."""
    return {
        "strict": True,
        "debug": False,
        "sp": _sp_settings(),
        "idp": _idp_settings(),
        "security": {
            "wantAssertionsSigned": True,
            "wantMessagesSigned": False,
            "requestedAuthnContext": False,
        },
    }


def _prepare_request(*, https: bool, http_host: str, script_name: str, post_params: dict) -> dict[str, Any]:
    return {
        "https": "on" if https else "off",
        "http_host": http_host,
        "server_port": "443" if https else "80",
        "script_name": script_name,
        "get_data": {},
        "post_data": post_params,
    }


def build_login_redirect(*, https: bool, http_host: str, script_name: str) -> str:
    """Build the redirect URL to the IdP's SSO endpoint (AuthnRequest)."""
    from onelogin.saml2.auth import OneLogin_Saml2_Auth

    req = _prepare_request(https=https, http_host=http_host, script_name=script_name, post_params={})
    auth = OneLogin_Saml2_Auth(req, _settings_dict())
    return auth.login(return_to=None)


def _normalize_claims(name_id: str, attributes: dict[str, list]) -> dict[str, Any]:
    """Normalize SAML attribute statements into standard user claims."""
    if not name_id:
        raise ValueError("SAML assertion missing NameID")
    claims: dict[str, Any] = {"sub": name_id}
    for key, values in attributes.items():
        claims[key] = values[0] if len(values) == 1 else list(values)
    for alias, canonical in _ATTRIBUTE_ALIASES.items():
        if alias in claims and canonical not in claims:
            claims[canonical] = claims[alias]
    return claims


def process_acs_response(*, https: bool, http_host: str, script_name: str, post_params: dict) -> dict[str, Any]:
    """Validate SAMLResponse payload and return user claims dictionary."""
    from onelogin.saml2.auth import OneLogin_Saml2_Auth

    req = _prepare_request(https=https, http_host=http_host, script_name=script_name, post_params=post_params)
    auth = OneLogin_Saml2_Auth(req, _settings_dict())
    auth.process_response()
    errors = auth.get_errors()
    if errors:
        raise ValueError(f"SAML response invalid: {', '.join(errors)} ({auth.get_last_error_reason()})")
    if not auth.is_authenticated():
        raise ValueError("SAML response not authenticated")
    claims = _normalize_claims(auth.get_nameid(), auth.get_attributes())
    assertion_id = auth.get_last_assertion_id()
    if assertion_id:
        claims["_assertion_id"] = assertion_id
    return claims


def build_sp_metadata_xml() -> str:
    """Generate Service Provider metadata XML string."""
    from onelogin.saml2.settings import OneLogin_Saml2_Settings

    settings = OneLogin_Saml2_Settings({"strict": True, "sp": _sp_settings()}, sp_validation_only=True)
    metadata = settings.get_sp_metadata()
    errors = settings.validate_metadata(metadata)
    if errors:
        raise ValueError(f"invalid SP metadata: {', '.join(errors)}")
    return metadata
