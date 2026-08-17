"""Tests for Principals Listing Auth."""

from __future__ import annotations

from conftest import IDENTITY, _request


def test_principals_listing_rejects_anonymous_callers() -> None:
    status, body = _request("GET", f"{IDENTITY}/principals")
    # `make_principal_dependency` (`libs/holon_common/auth.py`) takes the
    # raw `Request` and checks the Authorization header first, falling
    # back to the `holon_session` cookie
    # raises its own explicit 401 rather than FastAPI's missing-header
    # 422 (that was the pre-cookie-migration shape).
    assert status == 401, body
    assert "authentication required" in str(body["detail"]), body


def test_principals_listing_accepts_any_authenticated_principal(jdoe_token: str, alice_token: str) -> None:
    """alice has no workspace access at all."""
    for token in (jdoe_token, alice_token):
        status, body = _request("GET", f"{IDENTITY}/principals", token=token)
        assert status == 200, body
        assert any(p["urn"].endswith(":user:jdoe") for p in body), body
        assert all("display_name" in p for p in body), body
