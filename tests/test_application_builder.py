"""Application Builder tests (`services/experience/app/application_builder.py`).

Proves ontology-linked validation, declared dependencies, versioning,
promotion, and dashboard/form surfaces. No running stack required (unit tests).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

import pytest
from conftest import EXPERIENCE, IDENTITY, _request


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


def _object_app_definition(*, include_close_account: bool = False) -> dict:
    action_refs = [{"action": "Customer.putOnCreditHold", "riskClass": "low"}]
    if include_close_account:
        action_refs.append({"action": "Customer.closeAccount", "riskClass": "high"})
    return {
        "surfaces": [{"type": "objectApp", "objectType": "Customer", "route": "/apps/test"}],
        "bindings": [{"component": "table", "objectType": "Customer"}, {"component": "detail", "objectType": "Customer"}],
        "actionRefs": action_refs,
    }


def test_create_draft_validates_and_computes_dependencies(jdoe_token: str) -> None:
    name = f"test-app-{uuid.uuid4().hex[:8]}"
    status, app = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token, body={"definition": _object_app_definition()}
    )
    assert status == 200, app
    assert app["version"] == 1, app
    assert app["status"] == "draft", app
    assert app["dependencies"] == {"objectTypes": ["Customer"], "actions": ["Customer.putOnCreditHold"]}, app


def test_undeclared_object_type_is_rejected(jdoe_token: str) -> None:
    name = f"test-app-{uuid.uuid4().hex[:8]}"
    bad_definition = {
        "surfaces": [{"type": "objectApp", "objectType": "NotARealObjectType", "route": "/apps/bad"}],
        "bindings": [],
        "actionRefs": [],
    }
    status, body = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token, body={"definition": bad_definition}
    )
    assert status == 400, body
    assert "NotARealObjectType" in body["detail"], body


def test_undeclared_action_is_rejected(jdoe_token: str) -> None:
    name = f"test-app-{uuid.uuid4().hex[:8]}"
    bad_definition = {
        "surfaces": [{"type": "objectApp", "objectType": "Customer", "route": "/apps/bad"}],
        "bindings": [],
        "actionRefs": [{"action": "Customer.notARealAction", "riskClass": "low"}],
    }
    status, body = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token, body={"definition": bad_definition}
    )
    assert status == 400, body
    assert "notARealAction" in body["detail"], body


def test_editing_a_draft_updates_in_place_but_editing_after_promotion_creates_a_new_version(jdoe_token: str) -> None:
    name = f"test-app-{uuid.uuid4().hex[:8]}"
    status, v1 = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token, body={"definition": _object_app_definition()}
    )
    assert status == 200 and v1["version"] == 1, v1

    # Editing the still-unpromoted draft updates it in place — no new version.
    status, v1_again = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token, body={"definition": _object_app_definition()}
    )
    assert status == 200 and v1_again["version"] == 1, v1_again

    status, promoted = _request("POST", f"{EXPERIENCE}/api/applications/{name}/promote", token=jdoe_token)
    assert status == 200, promoted
    assert promoted["status"] == "promoted", promoted
    assert promoted["promoted_at"] is not None, promoted

    # P3.D3 — the promoted version is immutable: further edits create v2, a new draft.
    status, v2 = _request(
        "POST",
        f"{EXPERIENCE}/api/applications/{name}",
        token=jdoe_token,
        body={"definition": _object_app_definition(include_close_account=True)},
    )
    assert status == 200, v2
    assert v2["version"] == 2, v2
    assert v2["status"] == "draft", v2

    # v2 is still a draft, so this promotes it for the first time...
    status, v2_promoted = _request("POST", f"{EXPERIENCE}/api/applications/{name}/promote", token=jdoe_token)
    assert status == 200 and v2_promoted["version"] == 2 and v2_promoted["status"] == "promoted", v2_promoted

    # ...and re-promoting the now-already-promoted v2 must fail.
    status, re_promote_error = _request("POST", f"{EXPERIENCE}/api/applications/{name}/promote", token=jdoe_token)
    assert status == 400, re_promote_error


def test_object_app_data_surface_serves_list_detail_and_gates_undeclared_actions(jdoe_token: str) -> None:
    name = f"test-app-{uuid.uuid4().hex[:8]}"
    status, app = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token, body={"definition": _object_app_definition()}
    )
    assert status == 200, app
    status, _ = _request("POST", f"{EXPERIENCE}/api/applications/{name}/promote", token=jdoe_token)
    assert status == 200

    status, listing = _request("GET", f"{EXPERIENCE}/api/applications/{name}/data", token=jdoe_token)
    assert status == 200, listing
    assert isinstance(listing, list) and len(listing) > 0, listing

    status, detail = _request("GET", f"{EXPERIENCE}/api/applications/{name}/data/1", token=jdoe_token)
    assert status == 200, detail
    assert detail["id"] == 1, detail

    marker = uuid.uuid4().hex
    status, declared = _request(
        "POST",
        f"{EXPERIENCE}/api/applications/{name}/data/1/actions/putOnCreditHold",
        token=jdoe_token,
        body={"reason": marker},
    )
    assert status == 200, declared
    assert declared["reason"] == marker, declared

    status, undeclared = _request(
        "POST",
        f"{EXPERIENCE}/api/applications/{name}/data/1/actions/closeAccount",
        token=jdoe_token,
        body={"reason": "should be blocked"},
    )
    assert status == 403, undeclared
    assert "did not declare" in undeclared["detail"], undeclared


def _full_definition_with_dashboard_and_form() -> dict:
    return {
        "surfaces": [
            {"type": "objectApp", "objectType": "Customer", "route": "/apps/test"},
            {
                "type": "dashboard",
                "route": "/apps/test/dashboard",
                "widgets": [
                    {"component": "kpi", "objectType": "Customer", "label": "Total customers"},
                    {"component": "table", "objectType": "Order", "label": "Recent orders"},
                ],
            },
            {
                "type": "form",
                "route": "/apps/test/hold-form",
                "action": "Customer.putOnCreditHold",
                "fields": [{"name": "reason", "type": "string", "required": True}],
            },
        ],
        "bindings": [{"component": "table", "objectType": "Customer"}, {"component": "detail", "objectType": "Customer"}],
        "actionRefs": [{"action": "Customer.putOnCreditHold", "riskClass": "low"}],
    }


def test_dashboard_surface_serves_kpi_and_table_widgets(jdoe_token: str) -> None:
    name = f"test-app-{uuid.uuid4().hex[:8]}"
    status, app = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token,
        body={"definition": _full_definition_with_dashboard_and_form()},
    )
    assert status == 200, app
    assert set(app["dependencies"]["objectTypes"]) == {"Customer", "Order"}, app

    status, dash = _request("GET", f"{EXPERIENCE}/api/applications/{name}/dashboard", token=jdoe_token)
    assert status == 200, dash
    widgets = dash["widgets"]
    assert widgets[0]["component"] == "kpi" and widgets[0]["value"] > 0, widgets
    assert widgets[1]["component"] == "table" and len(widgets[1]["rows"]) > 0, widgets


def test_form_surface_schema_validation_and_submission(jdoe_token: str) -> None:
    name = f"test-app-{uuid.uuid4().hex[:8]}"
    status, app = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token,
        body={"definition": _full_definition_with_dashboard_and_form()},
    )
    assert status == 200, app

    status, schema = _request("GET", f"{EXPERIENCE}/api/applications/{name}/form", token=jdoe_token)
    assert status == 200, schema
    assert schema["action"] == "Customer.putOnCreditHold", schema
    assert schema["fields"] == [{"name": "reason", "type": "string", "required": True}], schema

    status, missing = _request("POST", f"{EXPERIENCE}/api/applications/{name}/form/2", token=jdoe_token, body={})
    assert status == 400, missing
    assert "missing required field" in missing["detail"], missing

    status, wrong_type = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}/form/2", token=jdoe_token, body={"reason": 12345}
    )
    assert status == 400, wrong_type
    assert "must be of type" in wrong_type["detail"], wrong_type

    marker = uuid.uuid4().hex
    status, result = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}/form/2", token=jdoe_token, body={"reason": marker}
    )
    assert status == 200, result
    assert result["reason"] == marker, result


def test_form_referencing_an_undeclared_action_is_rejected(jdoe_token: str) -> None:
    name = f"test-app-{uuid.uuid4().hex[:8]}"
    bad_definition = {
        "surfaces": [
            {
                "type": "form",
                "route": "/bad",
                "action": "Customer.closeAccount",
                "fields": [{"name": "reason", "type": "string", "required": True}],
            }
        ],
        "bindings": [],
        "actionRefs": [{"action": "Customer.putOnCreditHold", "riskClass": "low"}],  # closeAccount not declared
    }
    status, body = _request("POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token, body={"definition": bad_definition})
    assert status == 400, body
    assert "closeAccount" in body["detail"], body


def test_form_with_invalid_field_type_is_rejected(jdoe_token: str) -> None:
    name = f"test-app-{uuid.uuid4().hex[:8]}"
    bad_definition = {
        "surfaces": [
            {
                "type": "form",
                "route": "/bad2",
                "action": "Customer.putOnCreditHold",
                "fields": [{"name": "reason", "type": "notarealtype", "required": True}],
            }
        ],
        "bindings": [],
        "actionRefs": [{"action": "Customer.putOnCreditHold", "riskClass": "low"}],
    }
    status, body = _request("POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token, body={"definition": bad_definition})
    assert status == 400, body
    assert "invalid type" in body["detail"], body


def _analytics_definition(object_type: str = "Customer") -> dict:
    return {
        "surfaces": [{"type": "analytics", "route": "/apps/test/analytics", "objectType": object_type}],
        "bindings": [], "actionRefs": [],
    }


def test_analytics_surface_creates_and_computes_dependencies(jdoe_token: str) -> None:
    name = f"test-app-{uuid.uuid4().hex[:8]}"
    status, app = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token, body={"definition": _analytics_definition()}
    )
    assert status == 200, app
    assert app["dependencies"]["objectTypes"] == ["Customer"], app


def test_analytics_execute_is_bounded_to_the_declared_object_type(jdoe_token: str) -> None:
    """The **analytics** surface: a real `ExecutionRequest` is
    accepted ad hoc (unlike every other surface's fixed read path), but
    only against the one ObjectType the surface declared — proxying
    through to a *different* ObjectType (Order, say, on a Customer-scoped
    surface) must be rejected before it ever reaches Knowledge.
    """
    name = f"test-app-{uuid.uuid4().hex[:8]}"
    status, app = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token, body={"definition": _analytics_definition()}
    )
    assert status == 200, app

    status, group_by = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}/analytics/execute", token=jdoe_token,
        body={"object_type": "Customer", "operation": "group_by", "group_by_property": "segment"},
    )
    assert status == 200, group_by
    assert len(group_by["results"]) > 0, group_by

    status, out_of_scope = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}/analytics/execute", token=jdoe_token,
        body={"object_type": "Order", "operation": "count", "filter_property": "status", "filter_value": "pending"},
    )
    assert status == 403, out_of_scope
    assert "scoped to" in out_of_scope["detail"], out_of_scope


def test_analytics_replay_proxies_through_and_analytics_masking_is_preserved(
    jdoe_token: str, kenji_token: str
) -> None:
    name = f"test-app-{uuid.uuid4().hex[:8]}"
    status, app = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token, body={"definition": _analytics_definition()}
    )
    assert status == 200, app

    status, run = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}/analytics/execute", token=jdoe_token,
        body={"object_type": "Customer", "operation": "filter", "filter_property": "id", "filter_value": "1"},
    )
    assert status == 200, run
    assert run["results"][0]["email"] is not None, run

    status, replayed = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}/analytics/{run['planHash']}/replay", token=jdoe_token,
    )
    assert status == 200, replayed
    assert replayed["reproducible"] is True, replayed

    # Same application, same declared scope, a different (ABAC-denied)
    # caller — Experience's proxy applies no masking of its own, so this
    # is really proving Knowledge's own R8.7 masking survives the
    # extra hop through Experience unchanged.
    status, masked = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}/analytics/execute", token=kenji_token,
        body={"object_type": "Customer", "operation": "filter", "filter_property": "id", "filter_value": "1"},
    )
    assert status == 200, masked
    assert masked["results"][0]["email"] is None, masked


def test_analytics_endpoints_require_the_surface_to_be_declared(jdoe_token: str) -> None:
    name = f"test-app-{uuid.uuid4().hex[:8]}"
    status, app = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}", token=jdoe_token, body={"definition": _object_app_definition()}
    )
    assert status == 200, app
    status, err = _request(
        "POST", f"{EXPERIENCE}/api/applications/{name}/analytics/execute", token=jdoe_token,
        body={"object_type": "Customer", "operation": "count", "filter_property": "id", "filter_value": "1"},
    )
    assert status == 400, err
    assert "no analytics surface" in err["detail"], err
