"""OIDC and SAML login endpoints."""
from __future__ import annotations

import os

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse

from holon_common import HolonError
from holon_common.audit import emit_audit

from .. import deps, federation
from .. import oidc as oidc_client
from .. import saml as saml_client
from ..deps import TENANT_ID, _complete_federated_login


router = APIRouter()


@router.get("/oidc/login")
async def oidc_login() -> dict:
    """Start OIDC authorization-code + PKCE. 404 when HOLON_OIDC_ISSUER unset."""
    if not oidc_client.oidc_enabled():
        raise HolonError.not_found('OidcNotConfigured', "OIDC is not configured")
    redirect_uri = os.environ.get(
        "HOLON_OIDC_REDIRECT_URI", "http://localhost:8001/oidc/callback"
    )
    return await oidc_client.build_authorize_url(deps.pool, redirect_uri=redirect_uri)



@router.get("/oidc/callback")
async def oidc_callback(code: str, state: str):
    if not oidc_client.oidc_enabled():
        raise HolonError.not_found('OidcNotConfigured', "OIDC is not configured")
    try:
        claims = await oidc_client.exchange_code(deps.pool, code=code, state=state)
    except Exception as exc:
        emit_audit(
            category="identity",
            action="identity.oidc.login",
            outcome="failure",
            tenant_id=TENANT_ID,
            reason=str(exc),
        )
        raise HolonError.unauthorized('OidcError', f"OIDC exchange failed: {exc}") from exc

    sub = str(claims.get("sub") or "")
    if not sub:
        emit_audit(
            category="identity",
            action="identity.oidc.login",
            outcome="failure",
            tenant_id=TENANT_ID,
            reason="OIDC claims missing sub",
        )
        raise HolonError.unauthorized('OidcError', "OIDC claims missing sub")

    tenant_id = federation.tenant_from_claims(claims, default_tenant=TENANT_ID)
    frontend = os.environ.get("HOLON_OIDC_POST_LOGIN_REDIRECT", "http://localhost:5173/objects")
    return await _complete_federated_login(
        protocol="oidc",
        external_id=sub,
        tenant_id=tenant_id,
        local_name=federation.local_name_from_claims(claims),
        display_name=federation.display_name_from_claims(claims),
        workspace_roles=federation.workspace_roles_from_claims(claims),
        frontend_redirect=frontend,
    )



@router.get("/saml/login")
async def saml_login(request: Request) -> RedirectResponse:
    """Start SAML SP-initiated SSO. 404 when no IdP metadata is configured."""
    if not saml_client.saml_enabled():
        raise HolonError.not_found('SamlNotConfigured', "SAML is not configured")
    url = saml_client.build_login_redirect(
        https=request.url.scheme == "https",
        http_host=request.url.hostname or "localhost",
        script_name=request.url.path,
    )
    return RedirectResponse(url=url, status_code=302)



@router.post("/saml/acs")
async def saml_acs(request: Request):
    """SAML Assertion Consumer Service — validates the IdP's signed
    response, then completes login via the same path OIDC uses."""
    if not saml_client.saml_enabled():
        raise HolonError.not_found('SamlNotConfigured', "SAML is not configured")
    form = await request.form()
    post_params = {key: value for key, value in form.items()}
    try:
        claims = saml_client.process_acs_response(
            https=request.url.scheme == "https",
            http_host=request.url.hostname or "localhost",
            script_name=request.url.path,
            post_params=post_params,
        )
    except Exception as exc:
        emit_audit(
            category="identity",
            action="identity.saml.login",
            outcome="failure",
            tenant_id=TENANT_ID,
            reason=str(exc),
        )
        raise HolonError.unauthorized('SamlError', f"SAML assertion invalid: {exc}") from exc

    assertion_id = claims.pop("_assertion_id", None)
    if assertion_id:
        await deps.pool.execute(
            "DELETE FROM saml_seen_assertion WHERE seen_at < now() - interval '1 day'"
        )
        inserted = await deps.pool.fetchval(
            "INSERT INTO saml_seen_assertion (assertion_id) VALUES ($1) "
            "ON CONFLICT DO NOTHING RETURNING assertion_id",
            assertion_id,
        )
        if inserted is None:
            raise HolonError.unauthorized("SamlError", "SAML assertion replayed")
    tenant_id = federation.tenant_from_claims(claims, default_tenant=TENANT_ID)
    frontend = os.environ.get(
        "HOLON_SAML_POST_LOGIN_REDIRECT",
        os.environ.get("HOLON_OIDC_POST_LOGIN_REDIRECT", "http://localhost:5173/objects"),
    )
    return await _complete_federated_login(
        protocol="saml",
        external_id=claims["sub"],
        tenant_id=tenant_id,
        local_name=federation.local_name_from_claims(claims),
        display_name=federation.display_name_from_claims(claims),
        workspace_roles=federation.workspace_roles_from_claims(claims),
        frontend_redirect=frontend,
    )



@router.get("/saml/metadata")
async def saml_metadata() -> Response:
    """SP metadata XML for the IdP-side setup — not gated on
    `saml_enabled()` since an operator configuring the IdP integration
    needs this before HOLON_SAML_IDP_METADATA_* can be set."""
    xml = saml_client.build_sp_metadata_xml()
    return Response(content=xml, media_type="application/xml")

