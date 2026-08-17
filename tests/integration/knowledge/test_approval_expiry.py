"""Tests for Approval Expiry."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import IDENTITY, KNOWLEDGE, _request, ontology_url, holon_url


# The background sweep runs every 5s (actions.sweep_expired_approvals_forever's
# default poll_interval)
# sweep picks it up.
EXPIRY_TTL_SECONDS = 2


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


def _request_close_account(jdoe_token: str, customer_id: int, *, ttl_seconds: int | None = None) -> int:
    body = {"reason": "approval expiry test"}
    if ttl_seconds is not None:
        body["ttl_seconds"] = ttl_seconds
    status, resp = _request(
        "POST", ontology_url(f"/objects/Customer/{customer_id}/actions/closeAccount"), token=jdoe_token, body=body
    )
    assert status == 200, resp
    assert resp["status"] == "pending_approval", resp
    return resp["approvalId"]


def _poll_status(msmith_token: str, approval_id: int, expected: str) -> dict:
    deadline = time.monotonic() + 30
    approval: dict = {}
    while time.monotonic() < deadline:
        status, approval = _request("GET", holon_url(f"/approvals/{approval_id}"), token=msmith_token)
        assert status == 200, approval
        if approval["status"] == expected:
            return approval
        time.sleep(1)
    pytest.fail(f"approval {approval_id} never reached status={expected}: {approval}")


def test_overdue_approval_expires_and_cannot_be_approved(jdoe_token: str, msmith_token: str) -> None:
    customer_id = 3  # Bluewave Retail — untouched by other test modules' Actions
    approval_id = _request_close_account(jdoe_token, customer_id, ttl_seconds=EXPIRY_TTL_SECONDS)

    _poll_status(msmith_token, approval_id, "expired")

    status, body = _request(
        "POST", holon_url(f"/approvals/{approval_id}/approve"), token=msmith_token, body={}
    )
    assert status == 409, body
    assert "expired" in body["detail"], body


def test_overdue_approval_cannot_be_rejected_either(jdoe_token: str, msmith_token: str) -> None:
    customer_id = 7  # Vertex Manufacturing — distinct from the other cases in this module
    approval_id = _request_close_account(jdoe_token, customer_id, ttl_seconds=EXPIRY_TTL_SECONDS)

    _poll_status(msmith_token, approval_id, "expired")

    status, body = _request(
        "POST", holon_url(f"/approvals/{approval_id}/reject"), token=msmith_token, body={}
    )
    assert status == 409, body
    assert "expired" in body["detail"], body


def test_normal_approval_is_unaffected_by_the_expiry_machinery(jdoe_token: str, msmith_token: str) -> None:
    customer_id = 10  # Halcyon Pharma — distinct from the other cases in this module
    approval_id = _request_close_account(jdoe_token, customer_id)

    status, decision = _request(
        "POST", holon_url(f"/approvals/{approval_id}/approve"), token=msmith_token, body={}
    )
    assert status == 200, decision
    assert decision["status"] == "approved", decision
    # `closeAccount`'s external step + compensation are async now (the
    # Automation Platform's Workflow Engine)
    # the approval went through (not blocked by expiry, this test's actual
    # point), not that the saga has finished; see test_saga_compensation.py
    # and test_automation_platform.py for the async outcome itself.
    assert decision["sagaStatus"] == "processing", decision
