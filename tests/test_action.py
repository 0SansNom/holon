"""End-to-end verification of the first Action — `Customer.putOnCreditHold`.
Black-box over HTTP. Requires the stack running (`make up`).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

IDENTITY = "http://localhost:8001"
KNOWLEDGE = "http://localhost:8003"

TENANT_ID = "acme"


def _request(method: str, url: str, *, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _token_for(principal_urn: str) -> str:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        local_name = principal_urn.rsplit(":", 1)[-1]
        status, body = _request(
            "POST",
            f"{IDENTITY}/token",
            body={"principal_urn": principal_urn, "client_secret": f"{local_name}-dev-secret"},
        )
        if status == 200:
            return body["access_token"]
        time.sleep(1.5)
    pytest.fail(f"could not mint a token for {principal_urn}")


@pytest.fixture(scope="session")
def jdoe_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:jdoe")


@pytest.fixture(scope="session")
def kenji_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:kenji")


@pytest.fixture(scope="session")
def alice_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:alice")


def _put_on_hold(token: str, customer_id: int, reason: str = "test"):
    return _request(
        "POST",
        f"{KNOWLEDGE}/objects/Customer/{customer_id}/actions/putOnCreditHold",
        token=token,
        body={"reason": reason},
    )


def test_editor_can_invoke_the_action_and_it_is_reflected_on_read(jdoe_token: str) -> None:
    status, result = _put_on_hold(jdoe_token, 2, reason="suspicious chargeback pattern")
    assert status == 200, result
    assert result["onHold"] is True
    assert result["action"] == "Customer.putOnCreditHold"
    assert result["riskLevel"] == "low"

    status, customer = _request("GET", f"{KNOWLEDGE}/objects/Customer/2", token=jdoe_token)
    assert status == 200
    assert customer["credit_hold"] is True
    assert customer["credit_hold_reason"] == "suspicious chargeback pattern"


def test_viewer_without_editor_is_denied_write_specifically(kenji_token: str) -> None:
    """kenji can read (see test_authorization.py's ABAC case aside) but is
    not a workspace editor — proves read and write are separate grants,
    not the same boundary re-tested.
    """
    status, body = _put_on_hold(kenji_token, 3)
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body


def test_tenant_member_without_any_workspace_relation_is_denied(alice_token: str) -> None:
    status, body = _put_on_hold(alice_token, 3)
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body


def test_action_on_nonexistent_customer_is_404(jdoe_token: str) -> None:
    status, body = _put_on_hold(jdoe_token, 9999)
    assert status == 404, body
