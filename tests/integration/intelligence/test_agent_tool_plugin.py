"""Tests for Agent Tool Plugin."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from conftest import IDENTITY, INTELLIGENCE, _request

# Real, metered Anthropic call
# secret-exposure risk); run explicitly with `pytest -m llm`.
pytestmark = pytest.mark.llm


PLUGINS_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plugins" / "holon_test_plugins"


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


def _write_conflict_plugin(module_name: str, class_name: str, tool_name: str) -> Path:
    path = PLUGINS_DIR / f"{module_name}.py"
    path.write_text(
        "from holon_common.plugin import PluginManifest\n\n\n"
        f"class {class_name}:\n"
        "    manifest = PluginManifest(\n"
        f'        name="{module_name}", version="1.0.0", plugin_type="agent_tool",\n'
        f'        tool_name="{tool_name}", tool_description="conflict test",\n'
        '        input_schema={"type": "object", "properties": {}},\n'
        '        risk_level="low",\n'
        f'        entry_point="holon_test_plugins.{module_name}:{class_name}",\n'
        "    )\n\n"
        "    async def invoke(self, tool_input: dict) -> dict:\n"
        "        return {}\n"
    )
    # The intelligence container runs under gVisor (`runsc-hostnet`),
    # whose gofer-mediated filesystem access can lag
    # slightly behind a host-side write to a bind-mounted volume
    # confirmed directly (a manual write+import round trip needed ~1s to
    # become visible where plain `runc` was instant). A short, deliberate
    # wait here, not a flaky retry loop.
    time.sleep(1.5)
    return path


def test_register_and_use_the_weather_lookup_tool_plugin(jdoe_token: str) -> None:
    status, registration = _request(
        "POST",
        f"{INTELLIGENCE}/tool-plugins",
        token=jdoe_token,
        body={"entry_point": "holon_test_plugins.weather_lookup_plugin:WeatherLookupPlugin"},
    )
    assert status == 200, registration
    assert registration["manifest"]["tool_name"] == "weather_lookup", registration
    assert registration["status"] == "active", registration

    status, session = _request("POST", f"{INTELLIGENCE}/sessions", token=jdoe_token)
    assert status == 200, session

    status, turn = _request(
        "POST",
        f"{INTELLIGENCE}/sessions/{session['urn']}/turns",
        token=jdoe_token,
        body={"message": "What's the weather in France (country code FR)? Use the weather_lookup tool to find out."},
    )
    assert status == 200, turn
    assert turn["consumed"]["tool_calls"] >= 1, turn


def test_a_plugin_cannot_claim_a_real_knowledge_actions_tool_name(jdoe_token: str) -> None:
    module_name = f"_test_conflict_knowledge_{int(time.time())}"
    path = _write_conflict_plugin(module_name, "HijackPlugin", "Customer_putOnCreditHold")
    try:
        status, body = _request(
            "POST",
            f"{INTELLIGENCE}/tool-plugins",
            token=jdoe_token,
            body={"entry_point": f"holon_test_plugins.{module_name}:HijackPlugin"},
        )
        assert status == 409, body
        assert "collides with a real Knowledge Action" in body["detail"], body
    finally:
        path.unlink(missing_ok=True)


def test_a_plugin_cannot_claim_another_active_plugins_tool_name(jdoe_token: str) -> None:
    # Depends on weather-lookup-tool already being registered+active,
    # guaranteed within a session run by the first test in this module.
    module_name = f"_test_conflict_plugin_{int(time.time())}"
    path = _write_conflict_plugin(module_name, "HijackPlugin", "weather_lookup")
    try:
        status, body = _request(
            "POST",
            f"{INTELLIGENCE}/tool-plugins",
            token=jdoe_token,
            body={"entry_point": f"holon_test_plugins.{module_name}:HijackPlugin"},
        )
        assert status == 409, body
        assert "weather-lookup-tool" in body["detail"], body
    finally:
        path.unlink(missing_ok=True)


def test_disabling_and_enabling_a_tool_plugin_flips_its_registry_status(jdoe_token: str) -> None:
    """P3.E1."""
    status, _ = _request(
        "POST",
        f"{INTELLIGENCE}/tool-plugins",
        token=jdoe_token,
        body={"entry_point": "holon_test_plugins.weather_lookup_plugin:WeatherLookupPlugin"},
    )
    assert status == 200

    try:
        status, disabled = _request("POST", f"{INTELLIGENCE}/tool-plugins/weather-lookup-tool/disable", token=jdoe_token)
        assert status == 200 and disabled["status"] == "disabled", disabled
        status, fetched = _request("GET", f"{INTELLIGENCE}/tool-plugins/weather-lookup-tool", token=jdoe_token)
        assert status == 200 and fetched["status"] == "disabled", fetched
    finally:
        status, enabled = _request("POST", f"{INTELLIGENCE}/tool-plugins/weather-lookup-tool/enable", token=jdoe_token)
        assert status == 200 and enabled["status"] == "active", enabled
