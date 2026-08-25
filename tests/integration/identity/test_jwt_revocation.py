"""JWT revocation: logout jti + disable principal kill outstanding tokens."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from conftest import IDENTITY, TENANT_ID, _request, _unique_name, ontology_url


VALIDATOR_URN = f"hl:{TENANT_ID}:global:service-account:knowledge-project-validator"


def _raw(method: str, path: str, *, body: dict | None = None, token: str | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{IDENTITY}{path}", data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _mint(urn: str, secret: str) -> str:
    status, body = _raw("POST", "/token", body={"principal_urn": urn, "client_secret": secret})
    assert status == 200, body
    return body["access_token"]


def _wait_knowledge_rejects(token: str, *, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        status, last = _request("GET", ontology_url("/objects/ProductReview"), token=token)
        if status == 401:
            return last
        time.sleep(0.4)
    raise AssertionError(f"knowledge still accepted revoked/disabled JWT: {last}")


def test_snapshot_is_sa_only(jdoe_token: str) -> None:
    status, body = _raw("GET", "/internal/revocation-snapshot", token=jdoe_token)
    assert status == 403, body

    sa_token = _mint(VALIDATOR_URN, "knowledge-project-validator-dev-secret")
    status, body = _raw("GET", "/internal/revocation-snapshot", token=sa_token)
    assert status == 200, body
    assert "disabled_principal_urns" in body
    assert "revoked_jtis" in body


def test_logout_revokes_bearer_on_identity_and_knowledge(msmith_token: str) -> None:
    local = _unique_name("logoutjti")
    secret = f"{local}-secret"
    status, created = _request(
        "POST",
        f"{IDENTITY}/principals",
        token=msmith_token,
        body={
            "tenant_id": TENANT_ID,
            "type": "user",
            "local_name": local,
            "display_name": "Logout JTI",
            "client_secret": secret,
        },
    )
    assert status == 201, created
    token = _mint(created["urn"], secret)

    status, who = _raw("GET", "/whoami", token=token)
    assert status == 200, who

    status, body = _raw("POST", "/logout", body={}, token=token)
    assert status == 200, body

    status, who = _raw("GET", "/whoami", token=token)
    assert status == 401, who
    assert who.get("errorName") == "TokenRevoked"

    rejected = _wait_knowledge_rejects(token)
    assert rejected.get("errorName") == "TokenRevoked"


def test_disable_principal_rejects_existing_jwt(msmith_token: str) -> None:
    local = _unique_name("disablejti")
    secret = f"{local}-secret"
    status, created = _request(
        "POST",
        f"{IDENTITY}/principals",
        token=msmith_token,
        body={
            "tenant_id": TENANT_ID,
            "type": "user",
            "local_name": local,
            "display_name": "Disable JWT",
            "client_secret": secret,
        },
    )
    assert status == 201, created
    token = _mint(created["urn"], secret)

    status, updated = _request(
        "POST",
        f"{IDENTITY}/principals/{created['urn']}/status",
        token=msmith_token,
        body={"status": "disabled"},
    )
    assert status == 200, updated

    status, who = _raw("GET", "/whoami", token=token)
    assert status == 401, who
    assert who.get("errorName") == "PrincipalDisabled"

    sa_token = _mint(VALIDATOR_URN, "knowledge-project-validator-dev-secret")
    status, snap = _raw("GET", "/internal/revocation-snapshot", token=sa_token)
    assert status == 200, snap
    assert created["urn"] in snap["disabled_principal_urns"]

    rejected = _wait_knowledge_rejects(token)
    assert rejected.get("errorName") == "PrincipalDisabled"
