"""BFF session: data-plane proxies require auth; cookie reaches Knowledge."""

from __future__ import annotations

import json
import uuid
import urllib.error
import urllib.request
from http.cookies import SimpleCookie

from conftest import EXPERIENCE, TENANT_ID


JDOE_URN = f"hl:{TENANT_ID}:global:user:jdoe"


def _raw(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict, object]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{EXPERIENCE}{path}", data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else {}), response.headers
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else {}), exc.headers


def test_data_plane_proxies_require_auth() -> None:
    for path in ("/api/knowledge/health", "/api/connectivity/health", "/api/intelligence/health"):
        status, body, _ = _raw("GET", path)
        assert status == 401, (path, body)
        assert body.get("errorName") == "AuthenticationRequired", body

    status, body, _ = _raw(
        "POST",
        "/api/identity/login",
        body={"principal_urn": JDOE_URN, "client_secret": "jdoe-dev-secret"},
    )
    assert status == 200, body


def test_session_cookie_reaches_application_data() -> None:
    status, body, headers = _raw(
        "POST",
        "/api/identity/login",
        body={"principal_urn": JDOE_URN, "client_secret": "jdoe-dev-secret"},
    )
    assert status == 200, body
    parsed = SimpleCookie()
    parsed.load(headers.get("Set-Cookie") or "")
    cookie = parsed["holon_session"].value
    cookie_header = {"Cookie": f"holon_session={cookie}"}

    status, health, _ = _raw("GET", "/api/knowledge/health", headers=cookie_header)
    assert status == 200, health

    app_name = f"cookie-app-{uuid.uuid4().hex[:8]}"
    definition = {
        "surfaces": [{"type": "objectApp", "objectType": "Customer", "route": "/apps/cookie"}],
        "bindings": [{"component": "table", "objectType": "Customer"}],
        "actionRefs": [{"action": "Customer.putOnCreditHold", "riskClass": "low"}],
    }
    status, created, _ = _raw(
        "POST",
        f"/api/applications/{app_name}",
        body={"definition": definition},
        headers=cookie_header,
    )
    assert status == 200, created

    status, data, _ = _raw("GET", f"/api/applications/{app_name}/data", headers=cookie_header)
    assert status == 200, data
    assert data, data
