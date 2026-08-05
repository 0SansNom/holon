"""Phase 3 Lot E — the **external agent tool** plugin type
(`services/intelligence/app/tool_plugin_registry.py`). Proves P3.E1
(manifest, activatable/deactivatable), P3.E2 (a plugin can't claim a
tool name a real Knowledge Action or another active plugin already
owns), and that the Agent Runtime genuinely dispatches to a plugin's own
`invoke()` for a tool with zero ontology backing — a real Claude call
chooses to use it, not a mocked tool-use block. Real, metered Anthropic
call — excluded from CI by default.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

# Real, metered Anthropic call — excluded from CI by default (cost +
# secret-exposure risk); run explicitly with `pytest -m llm`.
pytestmark = pytest.mark.llm

IDENTITY = "http://localhost:8001"
INTELLIGENCE = "http://localhost:8006"

TENANT_ID = "acme"
PLUGINS_DIR = Path(__file__).resolve().parent.parent / "services" / "intelligence" / "app" / "plugins"


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
        f'        entry_point="app.plugins.{module_name}:{class_name}",\n'
        "    )\n\n"
        "    async def invoke(self, tool_input: dict) -> dict:\n"
        "        return {}\n"
    )
    # The intelligence container runs under gVisor (`runsc-hostnet`,
    # ADR 025/R8.9), whose gofer-mediated filesystem access can lag
    # slightly behind a host-side write to a bind-mounted volume —
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
        body={"entry_point": "app.plugins.weather_lookup_plugin:WeatherLookupPlugin"},
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
            body={"entry_point": f"app.plugins.{module_name}:HijackPlugin"},
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
            body={"entry_point": f"app.plugins.{module_name}:HijackPlugin"},
        )
        assert status == 409, body
        assert "weather-lookup-tool" in body["detail"], body
    finally:
        path.unlink(missing_ok=True)


def test_disabling_and_enabling_a_tool_plugin_flips_its_registry_status(jdoe_token: str) -> None:
    """P3.E1 — activatable/deactivatable without redeploy. `_list_tools`
    filters strictly on `status = 'active'` (code-reviewable in
    `tool_plugin_registry.list_active_tool_plugins`), so asserting the
    registry's own status transition is a direct, sufficient proof —
    doesn't need a second real LLM call just to re-observe the same fact
    indirectly through model behavior.
    """
    status, _ = _request(
        "POST",
        f"{INTELLIGENCE}/tool-plugins",
        token=jdoe_token,
        body={"entry_point": "app.plugins.weather_lookup_plugin:WeatherLookupPlugin"},
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
