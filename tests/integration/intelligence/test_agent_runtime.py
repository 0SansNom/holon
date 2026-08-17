"""Tests for Agent Runtime."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

import pytest
from conftest import IDENTITY, INTELLIGENCE, KNOWLEDGE, _request, ontology_url, holon_url

# Real, metered Anthropic calls
# secret-exposure risk); run explicitly with `pytest -m llm`.
pytestmark = pytest.mark.llm


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


def _new_session(token: str) -> str:
    status, body = _request("POST", f"{INTELLIGENCE}/sessions", token=token)
    assert status == 200, body
    assert body["status"] == "running", body
    assert body["consumed"] == {"iterations": 0, "tool_calls": 0, "tokens": 0}, body
    return body["urn"]


def test_agent_session_low_risk_tool_call_applies_immediately(jdoe_token: str) -> None:
    session_urn = _new_session(jdoe_token)
    marker = uuid.uuid4().hex
    status, body = _request(
        "POST",
        f"{INTELLIGENCE}/sessions/{session_urn}/turns",
        token=jdoe_token,
        body={"message": f"Put customer 7 on credit hold, reason: {marker}"},
    )
    assert status == 200, body
    assert body["status"] == "completed", body
    assert body["consumed"]["tool_calls"] >= 1, body

    status, customer = _request("GET", ontology_url("/objects/Customer/7"), token=jdoe_token)
    assert status == 200
    assert customer["credit_hold"] is True, customer
    assert customer["credit_hold_reason"] == marker, customer


def test_agent_session_high_risk_tool_call_requires_approval(jdoe_token: str, msmith_token: str) -> None:
    session_urn = _new_session(jdoe_token)
    marker = uuid.uuid4().hex
    status, body = _request(
        "POST",
        f"{INTELLIGENCE}/sessions/{session_urn}/turns",
        token=jdoe_token,
        body={"message": f"Close the account for customer 8, reason: {marker}"},
    )
    assert status == 200, body
    assert body["consumed"]["tool_calls"] >= 1, body

    status, approvals = _request("GET", holon_url("/approvals?status=pending"), token=msmith_token)
    assert status == 200, approvals
    matching = [a for a in approvals if a["reason"] == marker]
    assert len(matching) == 1, (marker, approvals)
    assert matching[0]["action_name"] == "Customer.closeAccount", matching[0]
    assert matching[0]["status"] == "pending", matching[0]


def test_agent_replay_reconstructs_the_pinned_context(jdoe_token: str) -> None:
    session_urn = _new_session(jdoe_token)
    status, body = _request(
        "POST", f"{INTELLIGENCE}/sessions/{session_urn}/turns", token=jdoe_token, body={"message": "Tell me about customer 1"}
    )
    assert status == 200, body

    status, replay = _request("POST", f"{INTELLIGENCE}/sessions/{session_urn}/replay", token=jdoe_token)
    assert status == 200, replay
    assert replay["originalText"].strip(), replay
    assert replay["replayedText"].strip(), replay


def test_session_belongs_to_the_agent_that_created_it(jdoe_token: str, msmith_token: str) -> None:
    session_urn = _new_session(jdoe_token)
    status, body = _request("GET", f"{INTELLIGENCE}/sessions/{session_urn}", token=msmith_token)
    assert status == 404, body
