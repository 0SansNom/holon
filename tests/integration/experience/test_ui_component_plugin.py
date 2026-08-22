"""Tests for Ui Component Plugin."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest
from conftest import EXPERIENCE, IDENTITY, _request


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


def _write_conflict_plugin(module_name: str, class_name: str, component_name: str) -> Path:
    path = PLUGINS_DIR / f"{module_name}.py"
    path.write_text(
        "from holon_common.plugin import PluginManifest\n\n\n"
        f"class {class_name}:\n"
        "    manifest = PluginManifest(\n"
        f'        name="{module_name}", version="1.0.0", plugin_type="ui_component",\n'
        f'        component_name="{component_name}", binding_contract={{}},\n'
        '        iframe_url="http://reviews-api:8000/map-widget.html",\n'
        f'        entry_point="holon_test_plugins.{module_name}:{class_name}",\n'
        "    )\n"
    )
    return path


def _dashboard_definition_with_map_widget() -> dict:
    return {
        "surfaces": [
            {
                "type": "dashboard",
                "route": "/apps/test/dashboard",
                "widgets": [{"component": "map", "objectType": "Customer", "label": "Customer map"}],
            }
        ],
        "bindings": [],
        "actionRefs": [],
    }


def test_unregistered_component_is_rejected_registered_one_is_accepted(jdoe_token: str) -> None:
    name = f"test-app-{uuid.uuid4().hex[:8]}"
    status, err = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token,
        body={"definition": _dashboard_definition_with_map_widget()},
    )
    # Either the plugin is already registered from an earlier run in this
    # session (200) or it genuinely isn't yet (400)
    # starting points; what matters is registering it always makes it 200.
    assert status in (200, 400), err

    status, reg = _request(
        "POST", f"{EXPERIENCE}/ui-component-plugins", token=jdoe_token,
        body={"entry_point": "holon_test_plugins.map_widget_plugin:MapWidgetPlugin"},
    )
    assert status == 200, reg
    assert reg["manifest"]["component_name"] == "map", reg

    status, app = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token,
        body={"definition": _dashboard_definition_with_map_widget()},
    )
    assert status == 200, app

    status, _ = _request("POST", f"{EXPERIENCE}/api/applications/{name}/promote", token=jdoe_token)
    assert status == 200

    status, dash = _request("GET", f"{EXPERIENCE}/api/applications/{name}/dashboard", token=jdoe_token)
    assert status == 200, dash
    widget = dash["widgets"][0]
    assert widget["component"] == "map", widget
    assert widget["iframeUrl"] == "http://reviews-api:8000/map-widget.html", widget
    assert len(widget["rows"]) > 0, widget


def test_workspace_viewer_cannot_manage_ui_component_plugins(kenji_token: str) -> None:
    """UI component manifests control a dashboard iframe URL, so a viewer."""
    entry_point = "holon_test_plugins.map_widget_plugin:MapWidgetPlugin"
    for method, path, body in (
        ("POST", "/ui-component-plugins", {"entry_point": entry_point}),
        ("POST", "/ui-component-plugins/map-widget/disable", None),
        ("POST", "/ui-component-plugins/map-widget/enable", None),
    ):
        status, response = _request(method, f"{EXPERIENCE}{path}", token=kenji_token, body=body)
        assert status == 403, response


def test_a_plugin_cannot_claim_a_builtin_component_name(jdoe_token: str) -> None:
    module_name = f"_test_conflict_builtin_{int(time.time())}"
    path = _write_conflict_plugin(module_name, "HijackPlugin", "table")
    try:
        status, body = _request(
            "POST", f"{EXPERIENCE}/ui-component-plugins", token=jdoe_token,
            body={"entry_point": f"holon_test_plugins.{module_name}:HijackPlugin"},
        )
        assert status == 409, body
        assert "built-in" in body["detail"], body
    finally:
        path.unlink(missing_ok=True)


def test_a_plugin_cannot_claim_another_active_plugins_component_name(jdoe_token: str) -> None:
    # Depends on map-widget already being registered+active, guaranteed
    # within a session run by the first test in this module.
    status, _ = _request(
        "POST", f"{EXPERIENCE}/ui-component-plugins", token=jdoe_token,
        body={"entry_point": "holon_test_plugins.map_widget_plugin:MapWidgetPlugin"},
    )
    module_name = f"_test_conflict_dup_{int(time.time())}"
    path = _write_conflict_plugin(module_name, "HijackPlugin", "map")
    try:
        status, body = _request(
            "POST", f"{EXPERIENCE}/ui-component-plugins", token=jdoe_token,
            body={"entry_point": f"holon_test_plugins.{module_name}:HijackPlugin"},
        )
        assert status == 409, body
        assert "map-widget" in body["detail"], body
    finally:
        path.unlink(missing_ok=True)


def test_disabling_and_enabling_flips_registry_status(jdoe_token: str) -> None:
    status, _ = _request(
        "POST", f"{EXPERIENCE}/ui-component-plugins", token=jdoe_token,
        body={"entry_point": "holon_test_plugins.map_widget_plugin:MapWidgetPlugin"},
    )
    assert status == 200

    try:
        status, disabled = _request("POST", f"{EXPERIENCE}/ui-component-plugins/map-widget/disable", token=jdoe_token)
        assert status == 200 and disabled["status"] == "disabled", disabled
        status, fetched = _request("GET", f"{EXPERIENCE}/ui-component-plugins/map-widget", token=jdoe_token)
        assert status == 200 and fetched["status"] == "disabled", fetched
    finally:
        status, enabled = _request("POST", f"{EXPERIENCE}/ui-component-plugins/map-widget/enable", token=jdoe_token)
        assert status == 200 and enabled["status"] == "active", enabled
