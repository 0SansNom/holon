"""End-to-end verification of human-in-the-loop approval for a high-risk Action.
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
def msmith_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:msmith")


def _request_close_account(token: str, customer_id: int, reason: str = "fraud investigation"):
    return _request(
        "POST", f"{KNOWLEDGE}/objects/Customer/{customer_id}/actions/closeAccount", token=token, body={"reason": reason}
    )


def test_high_risk_action_only_creates_a_pending_approval(jdoe_token: str) -> None:
    status, result = _request_close_account(jdoe_token, 9)
    assert status == 200, result
    assert result["status"] == "pending_approval"
    assert result["riskLevel"] == "high"
    assert "approvalId" in result

    status, customer = _request("GET", f"{KNOWLEDGE}/objects/Customer/9", token=jdoe_token)
    assert status == 200
    assert customer["account_closed"] is False, "must not apply before approval"


def test_requester_cannot_approve_her_own_request(jdoe_token: str) -> None:
    status, result = _request_close_account(jdoe_token, 9, reason="second attempt")
    assert status == 200, result
    approval_id = result["approvalId"]

    status, body = _request("POST", f"{KNOWLEDGE}/approvals/{approval_id}/approve", token=jdoe_token, body={})
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body


def test_admin_approval_applies_the_mutation(jdoe_token: str, msmith_token: str) -> None:
    status, result = _request_close_account(jdoe_token, 4, reason="confirmed fraud")
    assert status == 200, result
    approval_id = result["approvalId"]

    status, approval = _request("GET", f"{KNOWLEDGE}/approvals/{approval_id}", token=msmith_token)
    assert status == 200
    assert approval["status"] == "pending"

    status, decision = _request(
        "POST", f"{KNOWLEDGE}/approvals/{approval_id}/approve", token=msmith_token, body={"note": "confirmed with fraud team"}
    )
    assert status == 200, decision
    assert decision["status"] == "approved"
    assert decision["accountClosed"] is True

    status, customer = _request("GET", f"{KNOWLEDGE}/objects/Customer/4", token=jdoe_token)
    assert status == 200
    assert customer["account_closed"] is True
    assert customer["account_closed_reason"] == "confirmed fraud"


def test_admin_rejection_leaves_state_unapplied(jdoe_token: str, msmith_token: str) -> None:
    status, result = _request_close_account(jdoe_token, 6, reason="unclear signal")
    assert status == 200, result
    approval_id = result["approvalId"]

    status, decision = _request(
        "POST", f"{KNOWLEDGE}/approvals/{approval_id}/reject", token=msmith_token, body={"note": "insufficient evidence"}
    )
    assert status == 200, decision
    assert decision["status"] == "rejected"

    status, customer = _request("GET", f"{KNOWLEDGE}/objects/Customer/6", token=jdoe_token)
    assert status == 200
    assert customer["account_closed"] is False

    status, approval = _request("GET", f"{KNOWLEDGE}/approvals/{approval_id}", token=msmith_token)
    assert status == 200
    assert approval["status"] == "rejected"


def test_low_risk_action_still_applies_immediately(jdoe_token: str) -> None:
    """Regression: risk_level genuinely branches behavior — putOnCreditHold
    must still bypass the approval gate entirely.
    """
    status, result = _request(
        "POST",
        f"{KNOWLEDGE}/objects/Customer/5/actions/putOnCreditHold",
        token=jdoe_token,
        body={"reason": "chargeback review"},
    )
    assert status == 200, result
    assert result["status"] == "applied"
    assert result["riskLevel"] == "low"
    assert "approvalId" not in result


def test_pending_approvals_are_listed(jdoe_token: str, msmith_token: str) -> None:
    status, result = _request_close_account(jdoe_token, 8, reason="listed for review")
    assert status == 200, result
    approval_id = result["approvalId"]

    status, pending = _request("GET", f"{KNOWLEDGE}/approvals?status=pending", token=msmith_token)
    assert status == 200
    assert any(a["id"] == approval_id for a in pending), pending
