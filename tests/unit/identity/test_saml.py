"""Tests for the SAML SP module — the parts testable without a real
signed assertion or the onelogin library (imported lazily inside
saml.py's functions, so the module itself loads fine without it)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "identity"))

from app import saml  # noqa: E402


def test_saml_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_SAML_IDP_METADATA_URL", raising=False)
    monkeypatch.delenv("HOLON_SAML_IDP_METADATA_XML", raising=False)
    assert saml.saml_enabled() is False


def test_saml_enabled_via_metadata_url(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_SAML_IDP_METADATA_URL", "https://idp.example.com/metadata")
    assert saml.saml_enabled() is True


def test_saml_enabled_via_inline_metadata_xml(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_SAML_IDP_METADATA_URL", raising=False)
    monkeypatch.setenv("HOLON_SAML_IDP_METADATA_XML", "<EntityDescriptor/>")
    assert saml.saml_enabled() is True


def test_sp_private_key_plain_value(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_SAML_SP_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----raw-----END PRIVATE KEY-----")
    assert saml._sp_private_key() == "-----BEGIN PRIVATE KEY-----raw-----END PRIVATE KEY-----"


def test_sp_private_key_resolves_env_ref(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_SAML_SP_PRIVATE_KEY", "env:MY_SAML_KEY")
    monkeypatch.setenv("MY_SAML_KEY", "resolved-key-value")
    monkeypatch.delenv("HOLON_SECRET_BACKEND", raising=False)
    assert saml._sp_private_key() == "resolved-key-value"


def test_normalize_claims_requires_name_id() -> None:
    try:
        saml._normalize_claims("", {})
    except ValueError as exc:
        assert "NameID" in str(exc)
    else:
        raise AssertionError("expected ValueError for empty NameID")


def test_normalize_claims_flattens_single_valued_attributes() -> None:
    claims = saml._normalize_claims("user-123", {"email": ["alice@example.com"], "department": ["eng"]})
    assert claims["sub"] == "user-123"
    assert claims["email"] == "alice@example.com"
    assert claims["department"] == "eng"


def test_normalize_claims_keeps_multi_valued_attributes_as_list() -> None:
    claims = saml._normalize_claims("user-123", {"groups": ["workspace:main", "workspace-admin:filiale"]})
    assert claims["groups"] == ["workspace:main", "workspace-admin:filiale"]


def test_normalize_claims_maps_common_attribute_aliases() -> None:
    claims = saml._normalize_claims(
        "user-123",
        {
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress": ["bob@example.com"],
            "memberOf": ["workspace:main"],
        },
    )
    assert claims["email"] == "bob@example.com"
    assert claims["groups"] == "workspace:main"


def test_normalize_claims_does_not_overwrite_existing_canonical_key() -> None:
    """If the IdP already sends a plain `email` attribute alongside a
    claims-schema alias, the plain one wins (first alias match only
    fills gaps, per _ATTRIBUTE_ALIASES iteration order)."""
    claims = saml._normalize_claims(
        "user-123",
        {
            "email": ["direct@example.com"],
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress": ["aliased@example.com"],
        },
    )
    assert claims["email"] == "direct@example.com"
