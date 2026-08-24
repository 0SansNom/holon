"""Tests for Agent App."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

import pytest
from conftest import EXPERIENCE, IDENTITY, INTELLIGENCE, TENANT_ID, _request


AGENT_URN = f"hl:{TENANT_ID}:global:agent:ingest-bot"


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
def ingest_bot_token() -> str:
    """The shared identity every agentApp session runs under."""
    return _token_for(AGENT_URN)


@pytest.fixture(scope="session")
def weather_lookup_registered(jdoe_token: str) -> None:
    """Registers the `weather_lookup` agent-tool-plugin so it's a real."""
    status, registration = _request(
        "POST",
        f"{INTELLIGENCE}/tool-plugins",
        token=jdoe_token,
        body={"entry_point": "holon_test_plugins.weather_lookup_plugin:WeatherLookupPlugin"},
    )
    assert status == 200, registration


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _agent_app_definition(*, tools: list[str], system_prompt: str = "test system prompt", budget: dict | None = None) -> dict:
    return {
        "surfaces": [{
            "type": "agentApp",
            "route": "/apps/agent-test",
            "tools": tools,
            "systemPrompt": system_prompt,
            "budget": budget if budget is not None else {},
        }],
        "bindings": [],
        "actionRefs": [],
    }


def test_unknown_tool_is_rejected_at_definition_time(jdoe_token: str) -> None:
    name = _unique_name("bad-tool-app")
    status, body = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token,
        body={"definition": _agent_app_definition(tools=[_unique_name("nonexistent_tool")])},
    )
    assert status == 400, body
    assert "unknown tool(s)" in body["detail"], body


def test_missing_system_prompt_is_rejected(jdoe_token: str) -> None:
    name = _unique_name("no-prompt-app")
    bad_definition = {
        "surfaces": [{"type": "agentApp", "route": "/x", "tools": [], "budget": {}}],
        "bindings": [], "actionRefs": [],
    }
    status, body = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token, body={"definition": bad_definition}
    )
    assert status == 400, body
    assert "systemPrompt" in body["detail"], body


def test_invalid_budget_key_is_rejected(jdoe_token: str) -> None:
    name = _unique_name("bad-budget-key-app")
    status, body = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token,
        body={"definition": _agent_app_definition(tools=[], budget={"not_a_real_budget_key": 5})},
    )
    assert status == 400, body
    assert "budget" in body["detail"], body


def test_non_positive_budget_value_is_rejected(jdoe_token: str) -> None:
    name = _unique_name("bad-budget-value-app")
    status, body = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token,
        body={"definition": _agent_app_definition(tools=[], budget={"max_iterations": 0})},
    )
    assert status == 400, body
    assert "max_iterations" in body["detail"], body


def test_a_real_action_tool_and_a_real_agent_tool_plugin_are_both_accepted(
    jdoe_token: str, weather_lookup_registered: None
) -> None:
    """`Customer_putOnCreditHold` (a Knowledge Action) and `weather_lookup`."""
    name = _unique_name("mixed-tools-app")
    status, body = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token,
        body={"definition": _agent_app_definition(tools=["Customer_putOnCreditHold", "weather_lookup"])},
    )
    assert status == 200, body
    assert body["definition"]["surfaces"][0]["tools"] == ["Customer_putOnCreditHold", "weather_lookup"], body


def test_app_without_an_agent_app_surface_cannot_open_a_session(jdoe_token: str) -> None:
    name = _unique_name("no-agent-app-surface")
    status, body = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token,
        body={"definition": {"surfaces": [{"type": "objectApp", "objectType": "Customer", "route": "/x"}],
                              "bindings": [], "actionRefs": []}},
    )
    assert status == 200, body
    status, err = _request("POST", f"{EXPERIENCE}/api/applications/{name}/agent-sessions", token=jdoe_token)
    assert status == 400, err
    assert "no agentApp surface" in err["detail"], err


def test_session_compiles_the_declared_tools_prompt_and_budget(
    jdoe_token: str, ingest_bot_token: str
) -> None:
    name = _unique_name("compiled-session-app")
    marker_prompt = f"You are a narrow test agent {uuid.uuid4().hex}."
    status, app = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token,
        body={"definition": _agent_app_definition(
            tools=["Customer_putOnCreditHold"], system_prompt=marker_prompt,
            budget={"max_iterations": 3, "max_tool_calls": 4},
        )},
    )
    assert status == 200, app
    status, _ = _request("POST", f"{EXPERIENCE}/api/applications/{name}/promote", token=jdoe_token)
    assert status == 200

    status, session = _request("POST", f"{EXPERIENCE}/api/applications/{name}/agent-sessions", token=jdoe_token)
    assert status == 200, session
    assert session["agent_urn"] == AGENT_URN, session
    assert session["on_behalf_of"] == f"hl:{TENANT_ID}:global:user:jdoe", session
    assert session["budget"]["max_iterations"] == 3, session
    assert session["budget"]["max_tool_calls"] == 4, session
    # max_tokens wasn't declared
    # not be dropped or zeroed by the partial budget override.
    assert session["budget"]["max_tokens"] == 50_000, session

    # Read the session back directly, authenticated as the shared agent
    # identity itself
    # just what the create response echoed back.
    status, fetched = _request("GET", f"{INTELLIGENCE}/sessions/{session['urn']}", token=ingest_bot_token)
    assert status == 200, fetched
    assert fetched["allowed_tools"] == ["Customer_putOnCreditHold"], fetched
    assert fetched["system_prompt"] == marker_prompt, fetched


def test_only_the_launching_principal_can_drive_their_agent_app_session(
    jdoe_token: str, msmith_token: str
) -> None:
    name = _unique_name("owner-boundary-app")
    status, app = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token,
        body={"definition": _agent_app_definition(tools=[])},
    )
    assert status == 200, app
    status, _ = _request("POST", f"{EXPERIENCE}/api/applications/{name}/promote", token=jdoe_token)
    assert status == 200

    status, session = _request("POST", f"{EXPERIENCE}/api/applications/{name}/agent-sessions", token=jdoe_token)
    assert status == 200, session
    session_urn = session["urn"]

    # msmith never launched this session
    # Intelligence (no LLM call triggered by this test).
    status, denied = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}/agent-sessions/{session_urn}/turns",
        token=msmith_token, body={"message": "hello"},
    )
    assert status == 404, denied

    # A session_urn that was never recorded at all (wrong app, or just
    # made up) is rejected the same way, even for jdoe himself.
    status, missing = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}/agent-sessions/hl:{TENANT_ID}:global:agent-session:{uuid.uuid4().hex}/turns",
        token=jdoe_token, body={"message": "hello"},
    )
    assert status == 404, missing
