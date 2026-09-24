"""Tests for Action."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import IDENTITY, KNOWLEDGE, _request, ontology_url, holon_url


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


def _put_on_hold(token: str, customer_id: int, reason: str = "test"):
    return _request(
        "POST",
        ontology_url(f"/objects/Customer/{customer_id}/actions/putOnCreditHold"),
        token=token,
        body={"reason": reason},
    )


def test_editor_can_invoke_the_action_and_it_is_reflected_on_read(jdoe_token: str) -> None:
    status, result = _put_on_hold(jdoe_token, 2, reason="suspicious chargeback pattern")
    assert status == 200, result
    assert result["credit_hold"] is True
    assert result["action"] == "Customer.putOnCreditHold"
    assert result["riskLevel"] == "low"

    status, customer = _request("GET", ontology_url("/objects/Customer/2"), token=jdoe_token)
    assert status == 200
    assert customer["credit_hold"] is True
    assert customer["credit_hold_reason"] == "suspicious chargeback pattern"


def test_viewer_without_editor_is_denied_write_specifically(kenji_token: str) -> None:
    """kenji can read (see test_authorization.py's ABAC case aside) but is."""
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


def test_put_on_credit_hold_rejects_already_closed_account(
    jdoe_token: str, msmith_token: str
) -> None:
    """Semantic layer (submission_criteria), not the LLM / Pydantic schema."""
    # Exclusive from HITL's permanent close on customer 4; may already be
    # closed by saga's happy path — either way criteria must refuse hold.
    customer_id = 10  # Halcyon Pharma
    status, customer = _request("GET", ontology_url(f"/objects/Customer/{customer_id}"), token=jdoe_token)
    assert status == 200, customer
    if customer.get("account_closed") is not True:
        status, pending = _request(
            "POST",
            ontology_url(f"/objects/Customer/{customer_id}/actions/closeAccount"),
            token=jdoe_token,
            body={"reason": "criteria self-setup"},
        )
        assert status == 200, pending
        status, decision = _request(
            "POST",
            holon_url(f"/approvals/{pending['approvalId']}/approve"),
            token=msmith_token,
            body={},
        )
        assert status == 200, decision

    status, body = _put_on_hold(jdoe_token, customer_id, reason="should be refused")
    assert status == 400, body
    assert body["errorName"] == "ActionValidationFailed", body
    assert "closed" in body["detail"].lower(), body


def _order_rows(jdoe_token: str) -> list[dict]:
    status, body = _request("GET", ontology_url("/objects/Order"), token=jdoe_token)
    assert status == 200, body
    if isinstance(body, list):
        return body
    return list(body.get("data") or [])


def _cancellable_order_ids(jdoe_token: str) -> list[int]:
    """Pending + not already cancelled. Overlays survive `make seed` (ERP only)."""
    ids: list[int] = []
    for row in _order_rows(jdoe_token):
        if row.get("status") == "pending" and row.get("cancelled") is not True:
            ids.append(int(row["id"]))
    return ids


def _ensure_cancellable_order_id(jdoe_token: str) -> int:
    """Return a pending, non-cancelled Order id — uncancel one if needed."""
    ready = _cancellable_order_ids(jdoe_token)
    if ready:
        return ready[0]

    cancelled_pending = [
        int(r["id"])
        for r in _order_rows(jdoe_token)
        if r.get("status") == "pending" and r.get("cancelled") is True
    ]
    assert cancelled_pending, "fixtures need at least one pending Order"
    order_id = cancelled_pending[0]
    status, body = _request(
        "POST",
        ontology_url(f"/objects/Order/{order_id}/actions/uncancel"),
        token=jdoe_token,
        body={"reason": "reset cancel overlay for criteria test"},
    )
    assert status == 200, body
    assert body.get("cancelled") is False, body
    return order_id


def test_cancel_pending_order_is_semantic_not_just_syntax(jdoe_token: str) -> None:
    """Order.cancelPending: pending → applied; delivered → ActionValidationFailed."""
    order_id = _ensure_cancellable_order_id(jdoe_token)

    status, ok = _request(
        "POST",
        ontology_url(f"/objects/Order/{order_id}/actions/cancelPending"),
        token=jdoe_token,
        body={"reason": "customer withdrew"},
    )
    assert status == 200, ok
    assert ok["status"] == "applied", ok
    assert ok["cancelled"] is True, ok

    status, order = _request("GET", ontology_url(f"/objects/Order/{order_id}"), token=jdoe_token)
    assert status == 200, order
    assert order["cancelled"] is True, order
    assert order["status"] == "pending", order  # source status unchanged; overlay flags cancel

    # Delivered order (seed id=1) — syntax-valid tool call, semantic refuse.
    status, bad = _request(
        "POST",
        ontology_url("/objects/Order/1/actions/cancelPending"),
        token=jdoe_token,
        body={"reason": "should fail — already delivered"},
    )
    assert status == 400, bad
    assert bad["errorName"] == "ActionValidationFailed", bad
    assert "cancellable" in bad["detail"].lower() or "pending" in bad["detail"].lower(), bad

    status, delivered = _request("GET", ontology_url("/objects/Order/1"), token=jdoe_token)
    assert status == 200, delivered
    assert delivered["status"] == "delivered", delivered
    assert delivered.get("cancelled") is not True, delivered


def test_second_cancel_on_same_order_is_rejected(jdoe_token: str) -> None:
    order_id = _ensure_cancellable_order_id(jdoe_token)

    status, first = _request(
        "POST",
        ontology_url(f"/objects/Order/{order_id}/actions/cancelPending"),
        token=jdoe_token,
        body={"reason": "first cancel"},
    )
    assert status == 200, first
    assert first["cancelled"] is True, first

    status, second = _request(
        "POST",
        ontology_url(f"/objects/Order/{order_id}/actions/cancelPending"),
        token=jdoe_token,
        body={"reason": "second cancel"},
    )
    assert status == 400, second
    assert second["errorName"] == "ActionValidationFailed", second
