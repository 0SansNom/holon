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
