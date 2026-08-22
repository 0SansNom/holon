"""Tests for Execution And Export Plugins."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from conftest import IDENTITY, KNOWLEDGE, ontology_url, holon_url


PLUGINS_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plugins" / "holon_test_plugins"


def _request(method: str, url: str, *, token: str | None = None, body: dict | None = None, raw: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            content = response.read()
            return response.status, (content if raw else json.loads(content))
    except urllib.error.HTTPError as exc:
        content = exc.read()
        return exc.code, (content if raw else json.loads(content))


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


def _write_adapter_conflict_plugin(module_name: str, class_name: str, adapter_object_type: str) -> Path:
    path = PLUGINS_DIR / f"{module_name}.py"
    path.write_text(
        "from holon_common.plugin import PluginManifest\n\n\n"
        f"class {class_name}:\n"
        "    manifest = PluginManifest(\n"
        f'        name="{module_name}", version="1.0.0", plugin_type="execution_adapter",\n'
        f'        adapter_object_type="{adapter_object_type}",\n'
        f'        entry_point="holon_test_plugins.{module_name}:{class_name}",\n'
        "    )\n\n"
        "    async def execute(self, pool, *, object_type, tenant_id, filter_property, filter_value, operation):\n"
        "        return []\n"
    )
    return path


def _write_export_conflict_plugin(module_name: str, class_name: str, format_name: str) -> Path:
    path = PLUGINS_DIR / f"{module_name}.py"
    path.write_text(
        "from holon_common.plugin import PluginManifest\n\n\n"
        f"class {class_name}:\n"
        "    manifest = PluginManifest(\n"
        f'        name="{module_name}", version="1.0.0", plugin_type="export_format",\n'
        f'        format_name="{format_name}", content_type="text/plain",\n'
        f'        entry_point="holon_test_plugins.{module_name}:{class_name}",\n'
        "    )\n\n"
        "    def serialize(self, rows):\n"
        "        return b''\n"
    )
    return path


def test_register_execution_adapter_and_route_through_it(jdoe_token: str) -> None:
    status, registration = _request(
        "POST",
        holon_url("/execution-adapter-plugins"),
        token=jdoe_token,
        body={"entry_point": "holon_test_plugins.serving_store_adapter_plugin:ServingStoreAdapterPlugin"},
    )
    assert status == 200, registration
    assert registration["manifest"]["adapter_object_type"] == "Supplier", registration

    status, result = _request(
        "POST",
        holon_url("/execute"),
        token=jdoe_token,
        body={"object_type": "Supplier", "filter_property": "country", "filter_value": "FR"},
    )
    assert status == 200, result
    assert result["rowCount"] >= 1, result
    assert all(row["country"] == "FR" for row in result["results"]), result


def test_execution_adapter_cannot_claim_an_already_adapted_object_type(jdoe_token: str) -> None:
    module_name = f"_test_conflict_adapter_{int(time.time())}"
    path = _write_adapter_conflict_plugin(module_name, "HijackPlugin", "Supplier")
    try:
        status, body = _request(
            "POST",
            holon_url("/execution-adapter-plugins"),
            token=jdoe_token,
            body={"entry_point": f"holon_test_plugins.{module_name}:HijackPlugin"},
        )
        assert status == 409, body
        assert "serving-store-adapter" in body["detail"], body
    finally:
        path.unlink(missing_ok=True)


def test_disabling_and_enabling_execution_adapter_flips_registry_status(jdoe_token: str) -> None:
    status, _ = _request(
        "POST",
        holon_url("/execution-adapter-plugins"),
        token=jdoe_token,
        body={"entry_point": "holon_test_plugins.serving_store_adapter_plugin:ServingStoreAdapterPlugin"},
    )
    assert status == 200
    try:
        status, disabled = _request(
            "POST", holon_url("/execution-adapter-plugins/serving-store-adapter/disable"), token=jdoe_token
        )
        assert status == 200 and disabled["status"] == "disabled", disabled
    finally:
        status, enabled = _request(
            "POST", holon_url("/execution-adapter-plugins/serving-store-adapter/enable"), token=jdoe_token
        )
        assert status == 200 and enabled["status"] == "active", enabled


def test_register_export_format_and_export_via_it(jdoe_token: str) -> None:
    status, registration = _request(
        "POST",
        holon_url("/export-format-plugins"),
        token=jdoe_token,
        body={"entry_point": "holon_test_plugins.csv_export_plugin:CsvExportPlugin"},
    )
    assert status == 200, registration
    assert registration["manifest"]["format_name"] == "csv", registration

    status, body = _request("GET", ontology_url("/objects/Customer/export?format=csv"), token=jdoe_token, raw=True)
    assert status == 200, body
    text = body.decode()
    assert text.startswith("id,"), text
    assert "email" in text.splitlines()[0], text

    status, unknown = _request("GET", ontology_url("/objects/Customer/export?format=xml"), token=jdoe_token)
    assert status == 400, unknown


def test_export_inherits_r87_masking(kenji_token: str) -> None:
    status, body = _request("GET", ontology_url("/objects/Customer/export?format=csv"), token=kenji_token, raw=True)
    assert status == 200, body
    lines = body.decode().splitlines()
    header = lines[0].split(",")
    email_idx = header.index("email")
    first_row = lines[1].split(",")
    assert first_row[email_idx] == "", (header, first_row)


def test_export_format_cannot_claim_the_builtin_json_name(jdoe_token: str) -> None:
    module_name = f"_test_conflict_json_{int(time.time())}"
    path = _write_export_conflict_plugin(module_name, "HijackPlugin", "json")
    try:
        status, body = _request(
            "POST",
            holon_url("/export-format-plugins"),
            token=jdoe_token,
            body={"entry_point": f"holon_test_plugins.{module_name}:HijackPlugin"},
        )
        assert status == 409, body
        assert "built-in" in body["detail"], body
    finally:
        path.unlink(missing_ok=True)


def test_export_format_cannot_claim_another_active_plugins_name(jdoe_token: str) -> None:
    status, _ = _request(
        "POST",
        holon_url("/export-format-plugins"),
        token=jdoe_token,
        body={"entry_point": "holon_test_plugins.csv_export_plugin:CsvExportPlugin"},
    )
    assert status == 200
    module_name = f"_test_conflict_csv_{int(time.time())}"
    path = _write_export_conflict_plugin(module_name, "HijackPlugin", "csv")
    try:
        status, body = _request(
            "POST",
            holon_url("/export-format-plugins"),
            token=jdoe_token,
            body={"entry_point": f"holon_test_plugins.{module_name}:HijackPlugin"},
        )
        assert status == 409, body
        assert "csv-export" in body["detail"], body
    finally:
        path.unlink(missing_ok=True)
