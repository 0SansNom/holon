"""Tests for Declarative Actions."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import CONNECTIVITY, KNOWLEDGE, _request, _unique_name, ontology_url, holon_url, resync_and_wait_for_instance

REVIEWS_WITH_TAGS_API = "http://reviews-api:8000/reviews_with_tags.json"


def _register_sync_and_create_object_type(msmith_token: str, jdoe_token: str) -> str:
    """A fresh self-serve ObjectType per test."""
    source_name = _unique_name("declarative_reviews")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": source_name, "base_url": REVIEWS_WITH_TAGS_API},
    )
    assert status == 200, registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, result

    object_type_name = _unique_name("Review")
    status, object_type = _request(
        "POST", holon_url("/object-types"), token=msmith_token,
        body={
            "name": object_type_name,
            "source_dataset_urn": result["dataset_urn"],
            "property_mapping": {"id": "id", "comment": "comment"},
            "description": "pytest-created",
        },
    )
    assert status == 201, object_type
    resync_and_wait_for_instance(token=jdoe_token, dataset=source_name, object_type=object_type_name)
    return object_type_name


def _register_value_type(msmith_token: str, *, base_type: str = "string", format_regex: str | None = None) -> str:
    name = _unique_name("VT")
    status, body = _request(
        "POST", ontology_url("/valueTypes"), token=msmith_token,
        body={"name": name, "base_type": base_type, "format_regex": format_regex},
    )
    assert status == 201, body
    return name


def test_editor_cannot_create_an_action_type(jdoe_token: str) -> None:
    status, body = _request(
        "POST", ontology_url("/actionTypes"), token=jdoe_token,
        body={
            "name": _unique_name("Denied.doThing"), "target_object_type": "Review", "required_permission": "write",
            "risk_level": "low", "description": "should be denied", "edits": [{"property": "x", "source": "literal", "value": 1}],
        },
    )
    assert status == 403, body


def test_action_type_without_any_edits_is_400(msmith_token: str) -> None:
    status, body = _request(
        "POST", ontology_url("/actionTypes"), token=msmith_token,
        body={
            "name": _unique_name("Empty.doNothing"), "target_object_type": "Review", "required_permission": "write",
            "risk_level": "low", "description": "no edits at all", "edits": [],
        },
    )
    assert status == 400, body
    assert "exactly one of edits or edit_function" in body["detail"], body


def test_action_type_with_an_unknown_risk_level_is_400(msmith_token: str) -> None:
    status, body = _request(
        "POST", ontology_url("/actionTypes"), token=msmith_token,
        body={
            "name": _unique_name("Bad.riskLevel"), "target_object_type": "Review", "required_permission": "write",
            "risk_level": "medium", "description": "x", "edits": [{"property": "x", "source": "literal", "value": 1}],
        },
    )
    assert status == 400, body
    assert "risk_level" in body["detail"], body


def test_register_list_and_reject_duplicate_action_type(msmith_token: str) -> None:
    name = _unique_name("Demo.setStatus")
    status, created = _request(
        "POST", ontology_url("/actionTypes"), token=msmith_token,
        body={
            "name": name, "target_object_type": "Review", "required_permission": "write", "risk_level": "low",
            "description": "sets a status", "edits": [{"property": "recordStatus", "source": "literal", "value": "done"}],
        },
    )
    assert status == 201, created

    status, listed = _request("GET", holon_url("/actions"), token=msmith_token)
    assert status == 200, listed
    assert any(a["name"] == name for a in listed), listed

    status, fetched = _request("GET", holon_url(f"/actions/{name}"), token=msmith_token)
    assert status == 200 and fetched["name"] == name, fetched
    assert "_declarative" not in fetched, fetched  # internal apply-path detail, never in the public read shape

    status, dupe = _request(
        "POST", ontology_url("/actionTypes"), token=msmith_token,
        body={
            "name": name, "target_object_type": "Review", "required_permission": "write", "risk_level": "low",
            "description": "dupe", "edits": [{"property": "recordStatus", "source": "literal", "value": "done"}],
        },
    )
    assert status == 409, dupe


def test_a_low_risk_action_applies_immediately_and_is_visible_on_read(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    value_type_name = _register_value_type(msmith_token, format_regex="^(flagged|resolved)$")
    action_name = f"{object_type_name}.setModerationStatus"
    status, action_type = _request(
        "POST", ontology_url("/actionTypes"), token=msmith_token,
        body={
            "name": action_name, "target_object_type": object_type_name, "required_permission": "write",
            "risk_level": "low", "description": "sets moderation status, no code required",
            "parameters": [{"name": "status", "value_type": value_type_name, "required": True}],
            "edits": [{"property": "moderationStatus", "source": "parameter", "parameter_name": "status"}],
        },
    )
    assert status == 201, action_type

    status, result = _request(
        "POST", ontology_url(f"/objects/{object_type_name}/1/actions/{action_name}"), token=jdoe_token,
        body={"reason": "flag for review", "parameters": {"status": "flagged"}},
    )
    assert status == 200, result
    assert result["status"] == "applied", result
    assert result["moderationStatus"] == "flagged", result

    status, instance = _request("GET", ontology_url(f"/objects/{object_type_name}/1"), token=jdoe_token)
    assert status == 200 and instance["moderationStatus"] == "flagged", instance

    # Visible on the list route too, scoped to just this instance.
    status, listed = _request("GET", ontology_url(f"/objects/{object_type_name}"), token=jdoe_token)
    assert status == 200, listed
    by_id = {row["id"]: row for row in listed}
    assert by_id[1]["moderationStatus"] == "flagged", listed
    assert "moderationStatus" not in by_id[2], listed


def test_invoking_with_a_badly_formatted_parameter_is_400(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    value_type_name = _register_value_type(msmith_token, format_regex="^(flagged|resolved)$")
    action_name = f"{object_type_name}.setModerationStatus"
    status, _ = _request(
        "POST", ontology_url("/actionTypes"), token=msmith_token,
        body={
            "name": action_name, "target_object_type": object_type_name, "required_permission": "write",
            "risk_level": "low", "description": "x",
            "parameters": [{"name": "status", "value_type": value_type_name, "required": True}],
            "edits": [{"property": "moderationStatus", "source": "parameter", "parameter_name": "status"}],
        },
    )
    assert status == 201

    status, result = _request(
        "POST", ontology_url(f"/objects/{object_type_name}/1/actions/{action_name}"), token=jdoe_token,
        body={"reason": "x", "parameters": {"status": "not_a_valid_value"}},
    )
    assert status == 400, result
    assert result["errorName"] == "ActionValidationFailed", result
    assert "does not match" in result["detail"], result
    assert result["parameters"]["validation"]["result"] == "INVALID", result


def test_invoking_without_a_required_parameter_is_400(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    value_type_name = _register_value_type(msmith_token)
    action_name = f"{object_type_name}.requireStatus"
    status, _ = _request(
        "POST", ontology_url("/actionTypes"), token=msmith_token,
        body={
            "name": action_name, "target_object_type": object_type_name, "required_permission": "write",
            "risk_level": "low", "description": "x",
            "parameters": [{"name": "status", "value_type": value_type_name, "required": True}],
            "edits": [{"property": "recordStatus", "source": "parameter", "parameter_name": "status"}],
        },
    )
    assert status == 201

    status, result = _request(
        "POST", ontology_url(f"/objects/{object_type_name}/1/actions/{action_name}"), token=jdoe_token,
        body={"reason": "x", "parameters": {}},
    )
    assert status == 400, result
    assert result["errorName"] == "ActionValidationFailed", result
    assert "missing required parameter" in result["detail"], result


def test_a_violated_submission_criterion_is_400_and_nothing_is_applied(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    action_name = f"{object_type_name}.impossible"
    status, _ = _request(
        "POST", ontology_url("/actionTypes"), token=msmith_token,
        body={
            "name": action_name, "target_object_type": object_type_name, "required_permission": "write",
            "risk_level": "low", "description": "criterion can never pass",
            "edits": [{"property": "touched", "source": "literal", "value": True}],
            "submission_criteria": [{"property": "id", "operator": "gt", "value": 999999}],
        },
    )
    assert status == 201

    status, result = _request(
        "POST", ontology_url(f"/objects/{object_type_name}/1/actions/{action_name}"), token=jdoe_token,
        body={"reason": "x"},
    )
    assert status == 400, result
    assert result["errorName"] == "ActionValidationFailed", result
    assert "submission criterion failed" in result["detail"], result

    status, instance = _request("GET", ontology_url(f"/objects/{object_type_name}/1"), token=jdoe_token)
    assert status == 200 and "touched" not in instance, instance


def test_invoking_an_unknown_action_type_is_404(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    status, result = _request(
        "POST", ontology_url(f"/objects/{object_type_name}/1/actions/{_unique_name('never_registered')}"),
        token=jdoe_token, body={"reason": "x"},
    )
    assert status == 404, result


def test_high_risk_declarative_action_requires_approval_and_separation_of_duties(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    action_name = f"{object_type_name}.archive"
    status, _ = _request(
        "POST", ontology_url("/actionTypes"), token=msmith_token,
        body={
            "name": action_name, "target_object_type": object_type_name, "required_permission": "write",
            "risk_level": "high", "description": "archives permanently, needs approval",
            "edits": [{"property": "archived", "source": "literal", "value": True}],
        },
    )
    assert status == 201

    status, requested = _request(
        "POST", ontology_url(f"/objects/{object_type_name}/2/actions/{action_name}"), token=jdoe_token,
        body={"reason": "spam"},
    )
    assert status == 200, requested
    assert requested["status"] == "pending_approval", requested
    assert requested["target"]["objectType"] == object_type_name, requested
    assert "operationId" in requested, requested
    approval_id = requested["approvalId"]

    # Not yet applied.
    status, instance = _request("GET", ontology_url(f"/objects/{object_type_name}/2"), token=jdoe_token)
    assert status == 200 and "archived" not in instance, instance

    # jdoe (editor, the requester) cannot approve their own request.
    status, denied = _request("POST", holon_url(f"/approvals/{approval_id}/approve"), token=jdoe_token, body={})
    assert status == 403, denied

    # msmith (admin) can.
    status, approved = _request("POST", holon_url(f"/approvals/{approval_id}/approve"), token=msmith_token, body={})
    assert status == 200, approved
    assert approved["status"] == "approved", approved
    assert approved["archived"] is True, approved

    status, instance2 = _request("GET", ontology_url(f"/objects/{object_type_name}/2"), token=jdoe_token)
    assert status == 200 and instance2["archived"] is True, instance2


def test_customer_put_on_credit_hold_still_works_by_its_bare_local_name(jdoe_token: str) -> None:
    """`Customer.putOnCreditHold` is a declarative Action Type like any."""
    status, result = _request(
        "POST", ontology_url("/objects/Customer/1/actions/putOnCreditHold"), token=jdoe_token,
        body={"reason": "regression check"},
    )
    assert status == 200, result
    assert result["status"] == "applied", result
    assert result["credit_hold"] is True, result
    assert result["target"] == {"objectType": "Customer", "primaryKey": "1"}, result
    assert "operationId" in result, result


def test_ontology_preview_apply_and_batch_with_explicit_target(msmith_token: str, jdoe_token: str) -> None:
    """Action-first preview / apply / batch with explicit target (not parameters.id)."""
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    value_type_name = _register_value_type(msmith_token, format_regex="^(flagged|resolved)$")
    action_name = f"{object_type_name}.setModerationStatus"
    status, _ = _request(
        "POST", ontology_url("/actionTypes"), token=msmith_token,
        body={
            "name": action_name, "target_object_type": object_type_name, "required_permission": "write",
            "risk_level": "low", "description": "ontology apply surface",
            "parameters": [{"name": "status", "value_type": value_type_name, "required": True}],
            "edits": [{"property": "moderationStatus", "source": "parameter", "parameter_name": "status"}],
        },
    )
    assert status == 201

    status, preview = _request(
        "POST", ontology_url(f"/actions/{action_name}/preview"), token=jdoe_token,
        body={"target": {"primaryKey": "1"}, "parameters": {"status": "not_a_valid_value"}},
    )
    assert status == 200, preview
    assert preview["result"] == "INVALID", preview
    assert preview["target"]["primaryKey"] == "1", preview

    status, denied = _request(
        "POST", ontology_url(f"/actions/{action_name}"), token=jdoe_token,
        body={"target": {"primaryKey": "1"}, "parameters": {"status": "not_a_valid_value"}, "reason": "bad"},
    )
    assert status == 400, denied
    assert denied["errorName"] == "ActionValidationFailed", denied

    status, applied = _request(
        "POST", ontology_url(f"/actions/{action_name}"), token=jdoe_token,
        body={"target": {"primaryKey": "1"}, "parameters": {"status": "flagged"}, "reason": "ontology apply"},
    )
    assert status == 200, applied
    assert applied["status"] == "applied", applied
    assert applied["target"]["objectType"] == object_type_name, applied
    assert "operationId" in applied, applied

    status, instance_preview = _request(
        "POST", ontology_url(f"/objects/{object_type_name}/2/actions/{action_name}/preview"),
        token=jdoe_token, body={"parameters": {"status": "resolved"}},
    )
    assert status == 200, instance_preview
    assert instance_preview["result"] == "VALID", instance_preview

    status, batch = _request(
        "POST", ontology_url(f"/actions/{action_name}/batch"), token=jdoe_token,
        body={
            "reason": "ontology batch",
            "parameters": {"status": "resolved"},
            "targets": [{"primaryKey": "2"}, {"primaryKey": "1"}],
        },
    )
    assert status == 200, batch
    assert batch["succeeded"] == 2, batch
    assert batch["results"][0]["ok"] is True, batch
    assert batch["results"][0]["result"]["status"] == "applied", batch
