"""Tests for Plugin Sdk."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from conftest import CONNECTIVITY, IDENTITY, _request


PLUGINS_DIR = Path(__file__).resolve().parents[3] / "services" / "connectivity" / "app" / "plugins"


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


def _write_conflict_plugin(module_name: str, class_name: str, dataset_name: str) -> Path:
    path = PLUGINS_DIR / f"{module_name}.py"
    path.write_text(
        "from holon_common.plugin import PluginManifest\n\n\n"
        f"class {class_name}:\n"
        "    manifest = PluginManifest(\n"
        f'        name="{module_name}", version="1.0.0", plugin_type="connector",\n'
        f'        dataset_name="{dataset_name}", entry_point="app.plugins.{module_name}:{class_name}",\n'
        "    )\n\n"
        "    async def fetch(self) -> list[dict]:\n"
        "        return []\n"
    )
    return path


def test_registering_the_shipped_plugin_and_syncing_through_it_works(jdoe_token: str) -> None:
    status, registration = _request(
        "POST",
        f"{CONNECTIVITY}/plugins",
        token=jdoe_token,
        body={"entry_point": "app.plugins.exchange_rate_plugin:ExchangeRatePlugin"},
    )
    assert status == 200, registration
    assert registration["name"] == "exchange-rate-feed", registration
    assert registration["status"] == "active", registration
    assert registration["manifest"]["dataset_name"] == "exchange_rates", registration
    assert len(registration["checksum"]) == 64, registration  # sha256 hex digest

    status, sync_result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "exchange_rates"})
    assert status == 200, sync_result
    assert sync_result["row_count"] == 5, sync_result


def test_a_plugin_cannot_claim_a_seeded_demo_plugins_dataset(jdoe_token: str) -> None:
    module_name = f"_test_conflict_core_{int(time.time())}"
    path = _write_conflict_plugin(module_name, "CoreHijackPlugin", "customers")
    try:
        status, body = _request(
            "POST",
            f"{CONNECTIVITY}/plugins",
            token=jdoe_token,
            body={"entry_point": f"app.plugins.{module_name}:CoreHijackPlugin"},
        )
        assert status == 409, body
        assert "already claimed by active plugin" in body["detail"], body
    finally:
        path.unlink(missing_ok=True)


def test_a_plugin_cannot_claim_another_active_plugins_dataset(jdoe_token: str) -> None:
    # Depends on exchange-rate-feed already being registered+active, which
    # the first test in this module guarantees within a session run.
    module_name = f"_test_conflict_plugin_{int(time.time())}"
    path = _write_conflict_plugin(module_name, "PluginHijackPlugin", "exchange_rates")
    try:
        status, body = _request(
            "POST",
            f"{CONNECTIVITY}/plugins",
            token=jdoe_token,
            body={"entry_point": f"app.plugins.{module_name}:PluginHijackPlugin"},
        )
        assert status == 409, body
        assert "exchange-rate-feed" in body["detail"], body
    finally:
        path.unlink(missing_ok=True)


def test_disabling_a_plugin_blocks_sync_and_enabling_restores_it(jdoe_token: str) -> None:
    status, _ = _request(
        "POST",
        f"{CONNECTIVITY}/plugins",
        token=jdoe_token,
        body={"entry_point": "app.plugins.exchange_rate_plugin:ExchangeRatePlugin"},
    )
    assert status == 200

    try:
        status, disabled = _request("POST", f"{CONNECTIVITY}/plugins/exchange-rate-feed/disable", token=jdoe_token)
        assert status == 200 and disabled["status"] == "disabled", disabled

        status, blocked = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "exchange_rates"})
        assert status == 404, blocked
    finally:
        # Leave the shipped example plugin active
        # other tests expect.
        status, enabled = _request("POST", f"{CONNECTIVITY}/plugins/exchange-rate-feed/enable", token=jdoe_token)
        assert status == 200 and enabled["status"] == "active", enabled

    status, restored = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "exchange_rates"})
    assert status == 200, restored
