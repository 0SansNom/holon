"""JWT kid rotation + require_tenant_match — no stack required."""

from __future__ import annotations

import os
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
    require_urn_tenant_match(p, "hl:a:demo:object-type:Customer")
    with pytest.raises(HTTPException):
        require_urn_tenant_match(p, "hl:b:demo:object-type:Customer")


def test_jwt_kid_rotation(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_JWT_SECRETS", "old:secret-old,new:secret-new")
    monkeypatch.setenv("HOLON_JWT_ACTIVE_KID", "new")
    secrets, active = load_jwt_secrets()
    assert active == "new"
    p = Principal(urn="hl:acme:global:user:jdoe", type="user", tenant_id="acme", display_name="Jane")
    token = issue_token(p, secrets[active], kid=active, secrets=secrets)
    decoded = decode_token(token, secrets[active], secrets=secrets)
    assert decoded.urn == p.urn
    # Old kid still verifies historical tokens
    old_token = issue_token(p, secrets["old"], kid="old", secrets=secrets)
    assert decode_token(old_token, "unused", secrets=secrets).urn == p.urn
