"""`GET /principals` must not be anonymous.

The response carries every principal's `country`/`on_behalf_of` for the
whole tenant — the identity surface an attacker would want for
reconnaissance. The guard accepts either an `Authorization: Bearer`
header (CLI/scripts/service-to-service) or the `holon_session` HttpOnly
cookie (browser, since the JWT-in-localStorage -> HttpOnly-cookie
migration), any authenticated principal, viewer-tier included: the UI
resolves display names through this endpoint on many screens. So this
pins exactly two things: anonymous callers are rejected (401, see the
test below), any seeded principal's token gets 200. Black-box over HTTP,
same style as the other test modules. Requires the stack running
(`make up`).
"""

from __future__ import annotations

from conftest import IDENTITY, _request


def test_principals_listing_rejects_anonymous_callers() -> None:
    status, body = _request("GET", f"{IDENTITY}/principals")
    # `make_principal_dependency` (`libs/holon_common/auth.py`) takes the
    # raw `Request` and checks the Authorization header first, falling
    # back to the `holon_session` cookie — neither present here, so it
    # raises its own explicit 401 rather than FastAPI's missing-header
    # 422 (that was the pre-cookie-migration shape).
    assert status == 401, body
    assert "authentication required" in str(body["detail"]), body


def test_principals_listing_accepts_any_authenticated_principal(jdoe_token: str, alice_token: str) -> None:
    """alice has no workspace access at all — she still passes here,
    because the guard is authentication, not workspace-tier
    authorization (see the endpoint's own docstring).
    """
    for token in (jdoe_token, alice_token):
        status, body = _request("GET", f"{IDENTITY}/principals", token=token)
        assert status == 200, body
        assert any(p["urn"].endswith(":user:jdoe") for p in body), body
        assert all("display_name" in p for p in body), body
