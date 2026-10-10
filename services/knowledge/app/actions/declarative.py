"""Declarative Action Types execution engine for no-code actions."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg

from holon_common import Principal, build_urn, outbox

from .declarative_criteria import (  # noqa: F401
    ActionValidationError,
    _OPERATORS,
    _deep_set,
    _evaluate_criteria,
    _evaluate_one_criterion,
    _object_type_and_instance_id_from_instance_urn,
)
from .declarative_edits import (  # noqa: F401
    _apply_declarative_edits,
    _apply_instance_edit_rows,
    _compensate_declarative_action,
    _get_unmasked_instance,
    _write_instance_edits,
    revert_declarative_action,
)
from .hardcoded import _event
from .metrics import ACTION_EVENTS


async def validate_generic_action(
    pool: asyncpg.Pool,
    *,
    action_name: str,
    tenant_id: str,
    workspace_id: str,
    object_type: str,
    instance_id: str,
    principal: Principal,
    parameters: dict,
) -> dict:
    """Validate an action application without writing.

    Returns a report with top-level ``result`` ``VALID``|``INVALID`` and
    per-parameter evaluation entries. Raises ``LookupError`` only when the
    Action Type or target instance cannot be resolved.
    """
    from .. import ontology
    from ..action_structural import is_property_edit

    action_type = await ontology.get_action_type(pool, tenant_id, action_name)
    if action_type is None:
        raise LookupError(f"unknown Action Type: {action_name}")

    object_type_urn = ontology.object_type_urn(tenant_id, workspace_id, object_type)
    definition = await ontology.get_object_type(pool, object_type_urn)

    param_results: dict[str, dict] = {}
    messages: list[str] = []

    def _fail_param(name: str, message: str, *, required: bool = True) -> None:
        param_results[name] = {
            "result": "INVALID",
            "required": required,
            "evaluatedConstraints": [],
            "message": message,
        }
        messages.append(message)

    def _ok_param(name: str, *, required: bool = True) -> None:
        param_results.setdefault(
            name,
            {"result": "VALID", "required": required, "evaluatedConstraints": []},
        )

    target_interface = action_type.get("target_interface")
    if target_interface:
        if target_interface not in ((definition or {}).get("implements") or []):
            messages.append(
                f"{action_name} targets interface {target_interface!r}, which {object_type!r} does not implement"
            )
        else:
            interface = await ontology.get_interface_type(pool, tenant_id, target_interface)
            allowed_properties = set((interface or {}).get("required_properties") or [])
            for edit in action_type["edits"]:
                if not is_property_edit(edit):
                    messages.append(
                        f"{action_name} is scoped to interface {target_interface!r} and cannot use "
                        f"structural edit kind {edit.get('kind')!r} — only modify_property is allowed"
                    )
                elif edit["property"].split(".", 1)[0] not in allowed_properties and edit["property"] not in allowed_properties:
                    messages.append(
                        f"{action_name} is scoped to interface {target_interface!r} and cannot edit "
                        f"{edit['property']!r} — only the interface's required_properties are allowed"
                    )
    elif action_type["target_object_type"] != object_type:
        messages.append(f"{action_name} targets {action_type['target_object_type']!r}, not {object_type!r}")

    declared_parameters = {p["name"]: p for p in action_type["parameters"]}
    for name, declaration in declared_parameters.items():
        required = declaration.get("required", True)
        if required and name not in parameters:
            _fail_param(name, f"missing required parameter: {name!r}", required=True)
            continue
        if name not in parameters:
            _ok_param(name, required=required)
            continue
        value = parameters[name]
        if declaration.get("kind", "value_type") == "object_reference":
            referenced_type = declaration["object_type"]
            referenced_instance = await _get_unmasked_instance(
                pool, referenced_type, tenant_id, workspace_id, str(value)
            )
            if referenced_instance is None:
                _fail_param(name, f"parameter {name!r}: {referenced_type}/{value} does not exist", required=required)
                continue
            set_name = declaration.get("object_set")
            if set_name:
                set_urn = ontology.object_set_urn(tenant_id, workspace_id, set_name)
                obj_set = await ontology.get_object_set(pool, set_urn)
                if obj_set is None:
                    _fail_param(name, f"parameter {name!r}: unknown object_set {set_name!r}", required=required)
                    continue
                set_ot = str(obj_set["object_type_urn"]).rsplit(":", 1)[-1]
                if set_ot != referenced_type:
                    _fail_param(
                        name,
                        f"parameter {name!r}: object_set {set_name!r} targets {set_ot!r}, not {referenced_type!r}",
                        required=required,
                    )
                    continue
                set_ot_def = await ontology.get_object_type(pool, obj_set["object_type_urn"])
                mapping = (set_ot_def or {}).get("property_mapping") or {}
                if not ontology.matches_predicates(referenced_instance, obj_set["definition"], mapping):
                    _fail_param(
                        name,
                        f"parameter {name!r}: {referenced_type}/{value} is not in object set {set_name!r}",
                        required=required,
                    )
                    continue
            _ok_param(name, required=required)
            continue
        value_type = await ontology.get_value_type(pool, tenant_id, declaration["value_type"])
        if value_type is None:
            _fail_param(
                name,
                f"parameter {name!r} references unknown value_type {declaration['value_type']!r}",
                required=required,
            )
            continue
        error = ontology.validate_value(value, value_type)
        if error is not None:
            _fail_param(name, f"parameter {name!r}: {error}", required=required)
            continue
        _ok_param(name, required=required)

    for name, _value in parameters.items():
        if name == "reason":
            continue
        if name not in declared_parameters:
            _fail_param(name, f"unknown parameter: {name!r}", required=False)

    property_types = (definition or {}).get("property_types") or {}
    for edit in action_type["edits"]:
        if not is_property_edit(edit):
            continue
        prop_key = edit["property"]
        top_property = prop_key.split(".", 1)[0]
        rule = property_types.get(top_property)
        if rule is None:
            continue
        edit_value = parameters.get(edit["parameter_name"]) if edit["source"] == "parameter" else edit["value"]
        if rule.get("editable") is False:
            messages.append(f"property {top_property!r} is not editable")
            continue
        if "." in prop_key:
            continue
        if rule.get("required") and edit_value is None:
            messages.append(f"property {edit['property']!r} is required and cannot be set to null")
            continue
        if edit_value is not None and rule.get("kind") in ("value_type", "shared_property_type", "struct", "array"):
            type_error = await ontology.validate_typed_property_value(
                pool, tenant_id, rule, edit_value, property_name=edit["property"]
            )
            if type_error is not None:
                messages.append(type_error)

    instance_row = await _get_unmasked_instance(pool, object_type, tenant_id, workspace_id, instance_id)
    if instance_row is None:
        raise LookupError(f"{object_type}/{instance_id} not found")

    criteria_error = _evaluate_criteria(
        instance_row, action_type["submission_criteria"], principal=principal
    )
    submission_result = "VALID"
    if criteria_error is not None:
        ACTION_EVENTS.labels("criteria_reject", action_name).inc()
        messages.append(criteria_error)
        submission_result = "INVALID"

    any_param_invalid = any(p.get("result") == "INVALID" for p in param_results.values())
    result = "INVALID" if messages or any_param_invalid else "VALID"
    return {
        "result": result,
        "parameters": param_results,
        "submissionCriteriaResult": submission_result,
        "messages": messages,
        "actionType": action_type,
    }

async def request_generic_action(
    pool: asyncpg.Pool,
    *,
    action_name: str,
    tenant_id: str,
    workspace_id: str,
    object_type: str,
    instance_id: str,
    principal: Principal,
    reason: str,
    parameters: dict,
    ttl_seconds: Optional[int] = None,
) -> dict:
    """Apply a declarative Action Type after validation.

    Validation failures raise ``ActionValidationError`` (HTTP 400
    ``ActionValidationFailed`` on public routes).
    """
    from . import APPROVAL_TTL, _apply_now

    report = await validate_generic_action(
        pool,
        action_name=action_name,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        object_type=object_type,
        instance_id=instance_id,
        principal=principal,
        parameters=parameters,
    )
    if report["result"] != "VALID":
        raise ActionValidationError(report)

    action_type = report["actionType"]
    instance_urn = build_urn(tenant_id, workspace_id, "instance", f"{object_type}/{instance_id}")

    if action_type["risk_level"] == "low":
        return await _apply_now(
            pool, action_name, tenant_id, workspace_id, instance_urn, None, principal, reason,
            object_type=object_type, instance_id=instance_id, parameters=parameters,
        )

    ttl = timedelta(seconds=ttl_seconds) if ttl_seconds is not None else APPROVAL_TTL
    expires_at = datetime.now(timezone.utc) + ttl
    event = _event(
        event_type="knowledge.action.requested",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        instance_urn=instance_urn,
        actor=principal,
        payload={"action_name": action_name, "instance_urn": instance_urn, "reason": reason},
    )
    async with pool.acquire() as conn:
        async with conn.transaction():
            approval_id = await conn.fetchval(
                """
                INSERT INTO action_approval (tenant_id, action_name, instance_urn, requested_by_urn, reason, expires_at, parameters)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                RETURNING id
                """,
                tenant_id, action_name, instance_urn, principal.urn, reason, expires_at, json.dumps(parameters),
            )
            await outbox.enqueue(conn, event)

    from holon_common.audit import emit_audit

    emit_audit(
        category="action",
        action="knowledge.action.requested",
        outcome="pending",
        tenant_id=tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="instance",
        resource_urn=instance_urn,
        reason=reason,
        extra={"actionName": action_name, "approvalId": approval_id, "riskLevel": action_type["risk_level"]},
    )

    return {
        "status": "pending_approval",
        "approvalId": approval_id,
        "action": action_name,
        "riskLevel": action_type["risk_level"],
        "expiresAt": expires_at.isoformat(),
    }

