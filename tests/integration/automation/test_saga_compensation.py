"""Tests for Saga Compensation."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import CONNECTIVITY, IDENTITY, KNOWLEDGE, _request, ontology_url, holon_url


WORKSPACE_ID = "main"

# The documented test-only failure hook (services/connectivity/app/main.py's
# CLOSE_ACCOUNT_FAILURE_SENTINEL)
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


def test_approved_close_account_completes_the_saga(jdoe_token: str, msmith_token: str) -> None:
    customer_id = 4  # Kappa Foundries — untouched by the other case below
    approval_id = _request_close_account(jdoe_token, customer_id, "delinquent, saga happy path")

    status, decision = _request(
        "POST", holon_url(f"/approvals/{approval_id}/approve"), token=msmith_token, body={}
    )
    assert status == 200, decision
    assert decision["sagaStatus"] == "processing", decision  # Automation hasn't run yet — this is honest, not a failure
    assert decision["status"] == "approved", decision

    # Automation's async completion
    # same convergence pattern as every other async part of this suite.
    deadline = time.monotonic() + 30
    customer, source_row = {}, {}
    while time.monotonic() < deadline:
        status, customer = _request("GET", ontology_url(f"/objects/Customer/{customer_id}"), token=jdoe_token)
        assert status == 200
        status, source_row = _request("GET", f"{CONNECTIVITY}/source/customers/{customer_id}", token=jdoe_token)
        assert status == 200, source_row
        if customer["account_closed"] is True and source_row["account_closed"] is True:
            break
        time.sleep(1)
    assert customer["account_closed"] is True, customer
    assert source_row["account_closed"] is True, source_row


def test_failed_external_write_compensates_the_local_mutation(jdoe_token: str, msmith_token: str) -> None:
    customer_id = 5  # Solaris Energy Co — distinct from the happy-path customer
    approval_id = _request_close_account(jdoe_token, customer_id, FAILURE_SENTINEL)

    status, decision = _request(
        "POST", holon_url(f"/approvals/{approval_id}/approve"), token=msmith_token, body={}
    )
    assert status == 200, decision
    assert decision["sagaStatus"] == "processing", decision

    # Automation will call Connectivity (fails on the sentinel), then call
    # Knowledge back to compensate
    deadline = time.monotonic() + 30
    customer, approval = {}, {}
    while time.monotonic() < deadline:
        status, customer = _request("GET", ontology_url(f"/objects/Customer/{customer_id}"), token=jdoe_token)
        assert status == 200
        status, approval = _request("GET", holon_url(f"/approvals/{approval_id}"), token=msmith_token)
        assert status == 200
        if approval["status"] == "failed":
            break
        time.sleep(1)

    # Knowledge's own overlay must have been reverted, not left showing a
    # close that was actually rolled back.
    assert customer.get("account_closed") is not True, customer
    assert approval["status"] == "failed", approval

    # The source system must never actually have been changed
    # failed before Connectivity applied anything durable.
    status, source_row = _request("GET", f"{CONNECTIVITY}/source/customers/{customer_id}", token=jdoe_token)
    assert status == 200, source_row
    assert source_row["account_closed"] is False, source_row


def test_put_on_credit_hold_is_unaffected_by_the_saga_machinery(jdoe_token: str) -> None:
    """Regression check: `putOnCreditHold` is low-risk and never reaches."""
    customer_id = 6  # Meridian Logistics — untouched by either case above
    status, body = _request(
        "POST",
        ontology_url(f"/objects/Customer/{customer_id}/actions/putOnCreditHold"),
        token=jdoe_token,
        body={"reason": "regression check, no saga expected"},
    )
    assert status == 200, body
    assert body["status"] == "applied", body
    assert "sagaStatus" not in body, body
