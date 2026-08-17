"""Tests for Identity Auth."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.cookies import SimpleCookie

from conftest import IDENTITY, TENANT_ID


JDOE_URN = f"hl:{TENANT_ID}:global:user:jdoe"


def _raw_request(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict, object]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{IDENTITY}{path}", data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read()), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()), exc.headers


def test_login_cookie_auth_whoami_and_logout() -> None:
    status, body, headers = _raw_request(
        "POST",
        "/login",
        body={"principal_urn": JDOE_URN, "client_secret": "jdoe-dev-secret"},
    )
    assert status == 200, body

    set_cookie = headers.get("Set-Cookie")
    assert set_cookie
    parsed = SimpleCookie()
    parsed.load(set_cookie)
    session = parsed["holon_session"]
    assert session["httponly"]
    assert session["secure"]
    assert session["samesite"].lower() == "strict"

    cookie_header = {"Cookie": f"holon_session={session.value}"}
    status, principal, _ = _raw_request("GET", "/whoami", headers=cookie_header)
    assert status == 200, principal
    assert principal["urn"] == JDOE_URN

    status, body, headers = _raw_request("POST", "/logout", headers=cookie_header)
    assert status == 200, body
    assert "Max-Age=0" in headers.get("Set-Cookie", "")

    status, body, _ = _raw_request("GET", "/whoami")
    assert status == 401, body


def test_login_rejects_invalid_secret() -> None:
    status, body, headers = _raw_request(
        "POST",
        "/login",
        body={"principal_urn": JDOE_URN, "client_secret": "wrong"},
    )
    assert status == 401, body
    assert headers.get("Set-Cookie") is None
