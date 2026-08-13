"""JWT kid rotation + require_tenant_match — no stack required."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "libs"))

from holon_common.auth import (  # noqa: E402
    Principal,
    decode_token,
    issue_token,
    load_jwt_secrets,
    require_tenant_match,
    require_urn_tenant_match,
)


def test_require_tenant_match_denies_cross_tenant() -> None:
    p = Principal(urn="hl:a:global:user:x", type="user", tenant_id="a", display_name="X")
    with pytest.raises(HTTPException) as exc:
        require_tenant_match(p, "b")
    assert exc.value.status_code == 403


def test_require_urn_tenant_match() -> None:
    p = Principal(urn="hl:a:global:user:x", type="user", tenant_id="a", display_name="X")
    require_urn_tenant_match(p, "hl:a:main:object-type:Customer")
    with pytest.raises(HTTPException):
        require_urn_tenant_match(p, "hl:b:main:object-type:Customer")


def test_jwt_kid_rotation(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_ENV", raising=False)
    monkeypatch.setenv("HOLON_JWT_SECRETS", "old:secret-old,new:secret-new")
    monkeypatch.setenv("HOLON_JWT_ACTIVE_KID", "new")
    secrets, active = load_jwt_secrets()
    assert active == "new"
    p = Principal(urn="hl:acme:global:user:jdoe", type="user", tenant_id="acme", display_name="Jane")
    token = issue_token(p, secrets[active], kid=active, secrets=secrets, allow_user=True)
    decoded = decode_token(token, secrets[active], secrets=secrets)
    assert decoded.urn == p.urn
    old_token = issue_token(p, secrets["old"], kid="old", secrets=secrets, allow_user=True)
    assert decode_token(old_token, "unused", secrets=secrets).urn == p.urn


def test_issue_token_refuses_user_without_allow_user(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_ALLOW_LOCAL_USER_MINT", raising=False)
    monkeypatch.delenv("HOLON_ENV", raising=False)
    monkeypatch.delenv("HOLON_MINTABLE_PRINCIPAL_URNS", raising=False)
    p = Principal(urn="hl:a:global:user:x", type="user", tenant_id="a", display_name="X")
    with pytest.raises(ValueError, match="refusing to mint a user JWT"):
        issue_token(p, "secret")
    sa = Principal(urn="hl:a:global:service-account:bot", type="service_account", tenant_id="a", display_name="Bot")
    assert issue_token(sa, "secret")


def test_mint_allowlist_allows_local_name(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_ENV", raising=False)
    monkeypatch.setenv("HOLON_MINTABLE_PRINCIPAL_URNS", "connectivity-pipeline-runner")
    sa = Principal(
        urn="hl:acme:global:service-account:connectivity-pipeline-runner",
        type="service_account",
        tenant_id="acme",
        display_name="Runner",
    )
    assert issue_token(sa, "secret")


def test_mint_allowlist_refuses_other_sa(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_ENV", raising=False)
    monkeypatch.setenv("HOLON_MINTABLE_PRINCIPAL_URNS", "connectivity-pipeline-runner")
    sa = Principal(
        urn="hl:acme:global:service-account:other-bot",
        type="service_account",
        tenant_id="acme",
        display_name="Other",
    )
    with pytest.raises(ValueError, match="not in HOLON_MINTABLE_PRINCIPAL_URNS"):
        issue_token(sa, "secret")


def test_production_requires_mint_allowlist_for_sa(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_ENV", "production")
    monkeypatch.delenv("HOLON_MINTABLE_PRINCIPAL_URNS", raising=False)
    sa = Principal(
        urn="hl:acme:global:service-account:bot",
        type="service_account",
        tenant_id="acme",
        display_name="Bot",
    )
    with pytest.raises(ValueError, match="HOLON_MINTABLE_PRINCIPAL_URNS"):
        issue_token(sa, "secret")


def test_production_refuses_allow_user_without_flag(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_ENV", "production")
    monkeypatch.delenv("HOLON_ALLOW_USER_JWT_MINT", raising=False)
    monkeypatch.delenv("HOLON_ALLOW_LOCAL_USER_MINT", raising=False)
    p = Principal(urn="hl:a:global:user:x", type="user", tenant_id="a", display_name="X")
    with pytest.raises(ValueError, match="HOLON_ALLOW_USER_JWT_MINT"):
        issue_token(p, "secret", allow_user=True)


def test_identity_issuer_can_mint_any_authenticated_sa(monkeypatch) -> None:
    """Identity sets HOLON_ALLOW_USER_JWT_MINT and may mint SA/agent after /token auth."""
    monkeypatch.setenv("HOLON_ENV", "production")
    monkeypatch.setenv("HOLON_ALLOW_USER_JWT_MINT", "true")
    monkeypatch.setenv("HOLON_MINTABLE_PRINCIPAL_URNS", "")
    sa = Principal(
        urn="hl:acme:global:agent:ingest-bot",
        type="agent",
        tenant_id="acme",
        display_name="Bot",
    )
    assert issue_token(sa, "secret")
