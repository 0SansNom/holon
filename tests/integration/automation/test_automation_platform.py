"""Tests for Automation Platform."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import AUTOMATION, IDENTITY, KNOWLEDGE, _request, ontology_url, holon_url


FAILURE_SENTINEL = "__simulate_failure__"


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


def _request_close_account(jdoe_token: str, customer_id: int, reason: str) -> int:
    status, body = _request(
        "POST",
        ontology_url(f"/objects/Customer/{customer_id}/actions/closeAccount"),
        token=jdoe_token,
        body={"reason": reason},
    )
    assert status == 200, body
    assert body["status"] == "pending_approval", body
    return body["approvalId"]


def _poll_workflow_status(jdoe_token: str, approval_id: int, *, want: str) -> dict:
    deadline = time.monotonic() + 30
    execution: dict = {}
    while time.monotonic() < deadline:
        status, execution = _request(
            "GET", f"{AUTOMATION}/workflows/{approval_id}", token=jdoe_token
        )
        if status == 200 and execution["status"] == want:
            return execution
        time.sleep(1)
    pytest.fail(f"workflow_execution for approval {approval_id} never reached {want!r}: {execution}")


def test_health() -> None:
    status, body = _request("GET", f"{AUTOMATION}/health")
    assert status == 200
    assert body["status"] == "ok"


def test_workflow_engine_records_a_completed_execution(jdoe_token: str, msmith_token: str) -> None:
    customer_id = 8  # Orion Data Systems — untouched by test_saga_compensation.py's own customers
    approval_id = _request_close_account(jdoe_token, customer_id, "automation platform happy path")

    status, decision = _request(
        "POST", holon_url(f"/approvals/{approval_id}/approve"), token=msmith_token, body={}
    )
    assert status == 200, decision
    assert decision["sagaStatus"] == "processing", decision

    execution = _poll_workflow_status(jdoe_token, approval_id, want="completed")
    assert execution["action_name"] == "Customer.closeAccount", execution
    assert execution["error"] is None, execution


def test_workflow_engine_records_a_compensated_execution(jdoe_token: str, msmith_token: str) -> None:
    customer_id = 9  # Cedar & Finch — untouched by test_saga_compensation.py's own customers
    approval_id = _request_close_account(jdoe_token, customer_id, FAILURE_SENTINEL)

    status, decision = _request(
        "POST", holon_url(f"/approvals/{approval_id}/approve"), token=msmith_token, body={}
    )
    assert status == 200, decision
    assert decision["sagaStatus"] == "processing", decision

    execution = _poll_workflow_status(jdoe_token, approval_id, want="compensated")
    assert execution["action_name"] == "Customer.closeAccount", execution
    assert execution["error"], execution


def test_unknown_approval_is_404(jdoe_token: str) -> None:
    status, body = _request("GET", f"{AUTOMATION}/workflows/999999", token=jdoe_token)
    assert status == 404, body
