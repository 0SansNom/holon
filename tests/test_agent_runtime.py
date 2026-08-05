"""Phase 3 Lot B — Agent Runtime: extends `test_agent_delegation.py`'s
R8.8 scenario into a real tool-calling session. Two properties proven
live, with real LLM calls: a low-risk tool call applies immediately, and
a high-risk one produces a `pending` approval — the *exact* existing
Action machinery (`services/knowledge/app/actions.py`), reused as-is
through the agent, not a parallel agent-specific approval path. Requires
the stack running (`make up`), real LLM calls kept to two.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

import pytest

# Real, metered Anthropic calls — excluded from CI by default (cost +
# secret-exposure risk); run explicitly with `pytest -m llm`.
pytestmark = pytest.mark.llm

IDENTITY = "http://localhost:8001"
KNOWLEDGE = "http://localhost:8003"
INTELLIGENCE = "http://localhost:8006"

TENANT_ID = "acme"


def _request(method: str, url: str, *, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
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

    status, customer = _request("GET", f"{KNOWLEDGE}/objects/Customer/7", token=jdoe_token)
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

    status, approvals = _request("GET", f"{KNOWLEDGE}/approvals?status=pending", token=msmith_token)
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
