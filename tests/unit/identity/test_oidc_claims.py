"""Tests for Oidc Claims."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_FEDERATION_PATH = Path(__file__).resolve().parents[3] / "services" / "identity" / "app" / "federation.py"
_spec = importlib.util.spec_from_file_location("holon_identity_federation", _FEDERATION_PATH)
assert _spec and _spec.loader
federation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(federation)


def test_tenant_from_claims_direct_claim(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_OIDC_TENANT_CLAIM", "tenant_id")
    assert federation.tenant_from_claims({"tenant_id": "filiale-a"}, default_tenant="acme") == "filiale-a"


def test_tenant_from_claims_group_prefix(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_OIDC_TENANT_CLAIM", raising=False)
    monkeypatch.setenv("HOLON_OIDC_TENANT_GROUP_PREFIX", "tenant:")
    assert (
        federation.tenant_from_claims({"groups": ["tenant:filiale-b", "workspace:ops"]}, default_tenant="acme")
        == "filiale-b"
    )


def test_tenant_from_claims_fallback(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_OIDC_TENANT_CLAIM", "tenant_id")
    monkeypatch.setenv("HOLON_OIDC_TENANT_GROUP_PREFIX", "tenant:")
    assert federation.tenant_from_claims({"groups": ["eng"]}, default_tenant="acme") == "acme"
