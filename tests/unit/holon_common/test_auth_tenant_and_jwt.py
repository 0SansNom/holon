"""Tests for Auth Tenant And Jwt."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))

from holon_common.auth import (  # noqa: E402
    Principal,
    decode_token,
    issue_token,
    jwt_algorithm,
    load_jwt_secrets,
    load_jwt_verify_keys,
    require_tenant_match,
    require_urn_tenant_match,
)
from holon_common.errors import HolonError  # noqa: E402


def test_require_tenant_match_denies_cross_tenant() -> None:
    p = Principal(urn="hl:a:global:user:x", type="user", tenant_id="a", display_name="X")
    with pytest.raises(HolonError) as exc:
        require_tenant_match(p, "b")
    assert exc.value.status_code == 403
    assert exc.value.error_name == "TenantMismatch"


def test_require_urn_tenant_match() -> None:
    p = Principal(urn="hl:a:global:user:x", type="user", tenant_id="a", display_name="X")
    require_urn_tenant_match(p, "hl:a:main:object-type:Customer")
    with pytest.raises(HolonError):
        require_urn_tenant_match(p, "hl:b:main:object-type:Customer")


def test_jwt_kid_rotation(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_ENV", raising=False)
    monkeypatch.delenv("HOLON_JWT_ALG", raising=False)
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


def _rsa_pem_pair() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def test_jwt_rs256_roundtrip(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_ENV", raising=False)
    private_pem, public_pem = _rsa_pem_pair()
    monkeypatch.setenv("HOLON_JWT_ALG", "RS256")
    monkeypatch.setenv("HOLON_JWT_PRIVATE_KEYS", json.dumps({"k1": private_pem}))
    monkeypatch.setenv("HOLON_JWT_PUBLIC_KEYS", json.dumps({"k1": public_pem}))
    monkeypatch.setenv("HOLON_JWT_ACTIVE_KID", "k1")
    secrets, active = load_jwt_secrets()
    verify = load_jwt_verify_keys()
    assert active == "k1"
    assert "k1" in verify
    p = Principal(urn="hl:acme:global:user:jdoe", type="user", tenant_id="acme", display_name="Jane")
    token = issue_token(p, secrets[active], kid=active, secrets=secrets, allow_user=True)
    decoded = decode_token(token, "unused")
    assert decoded.urn == p.urn
    assert jwt_algorithm() == "RS256"


def test_jwt_rs256_rejects_unknown_kid(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_ENV", raising=False)
    private_pem, public_pem = _rsa_pem_pair()
    monkeypatch.setenv("HOLON_JWT_ALG", "RS256")
    monkeypatch.setenv("HOLON_JWT_PRIVATE_KEYS", json.dumps({"k1": private_pem}))
    monkeypatch.setenv("HOLON_JWT_PUBLIC_KEYS", json.dumps({"k1": public_pem}))
    secrets, _ = load_jwt_secrets()
    p = Principal(urn="hl:acme:global:service-account:bot", type="service_account", tenant_id="acme", display_name="Bot")
    token = issue_token(p, secrets["k1"], kid="k1", secrets=secrets)
    monkeypatch.setenv("HOLON_JWT_PUBLIC_KEYS", json.dumps({"other": public_pem}))
    with pytest.raises(HolonError) as exc:
        decode_token(token, "unused")
    assert exc.value.status_code == 401
    assert exc.value.error_name == "UnknownJwtKid"


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
