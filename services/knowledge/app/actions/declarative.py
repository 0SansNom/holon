"""Declarative Action Types (`ontology/action_types.py`'s registry) —
the no-code Action path. A leaf module: imports `_event` from
`.hardcoded` (one-way — this file depends on that one, never the reverse)
plus package-external modules (`ontology`, `core`, `resolver`,
`serving_store`), never from `__init__.py`. `__init__.py`'s shared
orchestration (`_apply_now`/`approve_action`) imports
`_apply_declarative_edits`/`_compensate_declarative_action` from here.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import asyncpg

from holon_common import HolonError, Principal, build_urn, outbox

from .hardcoded import _event
from .metrics import ACTION_EVENTS


class ActionValidationError(Exception):
    """Declarative Action failed parameter / criteria validation.

    Carry the full validation report so HTTP handlers can emit
    ``ActionValidationFailed`` with ``parameters.validation``.
    """

    def __init__(self, report: dict) -> None:
        self.report = report
        messages = report.get("messages") or []
        detail = messages[0] if messages else "action validation failed"
        super().__init__(detail)

    def to_holon_error(self) -> HolonError:
        return HolonError.invalid_argument(
            "ActionValidationFailed",
            str(self),
            validation={
                "result": self.report.get("result"),
                "parameters": self.report.get("parameters") or {},
                "submissionCriteriaResult": self.report.get("submissionCriteriaResult"),
                "messages": self.report.get("messages") or [],
            },
        )


_OPERATORS = {
    "eq": lambda actual, expected: actual == expected,
    "neq": lambda actual, expected: actual != expected,
    "gt": lambda actual, expected: actual is not None and actual > expected,
    "gte": lambda actual, expected: actual is not None and actual >= expected,
    "lt": lambda actual, expected: actual is not None and actual < expected,
    "lte": lambda actual, expected: actual is not None and actual <= expected,
    "in": lambda actual, expected: actual in (expected or []),
}


def _object_type_and_instance_id_from_instance_urn(instance_urn: str) -> tuple[str, str]:
    """Every `instance_urn` this package builds already has the
    `{ObjectType}/{id}` shape in its final segment.
    """
    local = instance_urn.rsplit(":", 1)[-1]
    object_type, instance_id = local.split("/", 1)
    return object_type, instance_id


def _evaluate_criteria(
    instance_row: dict,
    criteria: list[dict],
    *,
    principal: Optional[Principal] = None,
) -> Optional[str]:
    """Evaluate submission criteria. Flat list = implicit AND.

    Leaf kinds: property comparison, principal field comparison.
    Groups: ``all`` / ``any``. Optional ``message`` overrides the default
    failure string.
    """
    for criterion in criteria:
        error = _evaluate_one_criterion(instance_row, criterion, principal=principal)
        if error is not None:
            return error
    return None


def _evaluate_one_criterion(
    instance_row: dict,
    criterion: dict,
    *,
    principal: Optional[Principal],
) -> Optional[str]:
    custom = criterion.get("message")

    if "all" in criterion:
        for child in criterion["all"]:
            err = _evaluate_one_criterion(instance_row, child, principal=principal)
            if err is not None:
                return custom or err
        return None
    if "any" in criterion:
        errors: list[str] = []
        for child in criterion["any"]:
            err = _evaluate_one_criterion(instance_row, child, principal=principal)
            if err is None:
                return None
            errors.append(err)
        return custom or (errors[0] if errors else "submission criterion failed: any")

    if "principal" in criterion:
        if principal is None:
            return custom or "submission criterion failed: principal unavailable"
        field = criterion["principal"]
        actual = getattr(principal, field, None)
        operator = criterion["operator"]
        expected = criterion["value"]
        try:
            passed = _OPERATORS[operator](actual, expected)
        except (TypeError, KeyError):
            passed = False
        if not passed:
            return custom or f"submission criterion failed: principal.{field} {operator} {expected!r} (actual: {actual!r})"
        return None

    property_name = criterion["property"]
    operator = criterion["operator"]
    expected = criterion["value"]
    actual = instance_row.get(property_name)
    try:
        passed = _OPERATORS[operator](actual, expected)
    except TypeError:
        passed = False
    if not passed:
        return custom or f"submission criterion failed: {property_name} {operator} {expected!r} (actual: {actual!r})"
    return None


def _deep_set(obj: dict, path: list[str], value: Any) -> dict:
    """Return a copy of ``obj`` with ``path`` set to ``value`` (nested dicts)."""
    root = dict(obj)
    cur = root
    for key in path[:-1]:
        nxt = cur.get(key)
        cur[key] = dict(nxt) if isinstance(nxt, dict) else {}
        cur = cur[key]
    cur[path[-1]] = value
    return root


async def _write_instance_edits(
    conn: asyncpg.Connection,
    tenant_id: str,
    object_type: str,
    instance_id: str,
    resolved: dict[str, Any],
    *,
    action_urn: str,
    actor: Principal,
    at: datetime,
    base_instance: Optional[dict] = None,
) -> tuple[dict, dict]:
    """Upsert into ``object_instance_edit`` + prior-value capture.

    Keys may be dotted paths (``address.city``) — the top-level property
    is rewritten as a merged struct (P2d nested struct edit).
    """
    result: dict[str, Any] = {}
    prior: dict[str, Any] = {}

    # Collapse dotted paths into top-level property writes.
    top_level: dict[str, Any] = {}
    for key, value in resolved.items():
        if "." not in key:
            top_level[key] = value
            continue
        top, *rest = key.split(".")
        existing_val = top_level.get(top)
        if existing_val is None:
            # Seed from prior overlay / base instance when available.
            existing_row = await conn.fetchrow(
                "SELECT property_value FROM object_instance_edit WHERE tenant_id = $1 AND object_type = $2 "
                "AND instance_id = $3 AND property_name = $4",
                tenant_id, object_type, instance_id, top,
            )
            if existing_row is not None:
                existing_val = json.loads(existing_row["property_value"])
            elif base_instance is not None:
                existing_val = base_instance.get(top)
            if not isinstance(existing_val, dict):
                existing_val = {}
            top_level[top] = existing_val
        if not isinstance(top_level[top], dict):
            top_level[top] = {}
        top_level[top] = _deep_set(top_level[top], rest, value)

    for property_name, value in top_level.items():
        existing = await conn.fetchrow(
            "SELECT property_value FROM object_instance_edit WHERE tenant_id = $1 AND object_type = $2 "
            "AND instance_id = $3 AND property_name = $4 FOR UPDATE",
            tenant_id, object_type, instance_id, property_name,
        )
        prior[property_name] = {
            "existed": existing is not None,
            "value": json.loads(existing["property_value"]) if existing is not None else None,
        }
        await conn.execute(
            """
            INSERT INTO object_instance_edit
                (tenant_id, object_type, instance_id, property_name, property_value, set_by_action_urn, set_by_urn, set_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8)
            ON CONFLICT (tenant_id, object_type, instance_id, property_name) DO UPDATE SET
                property_value = EXCLUDED.property_value,
                set_by_action_urn = EXCLUDED.set_by_action_urn,
                set_by_urn = EXCLUDED.set_by_urn,
                set_at = EXCLUDED.set_at
            """,
            tenant_id, object_type, instance_id, property_name, json.dumps(value), action_urn, actor.urn, at,
        )
        result[property_name] = value
    return result, prior


async def _apply_declarative_edits(
    conn: asyncpg.Connection,
    tenant_id: str,
    object_type: str,
    instance_id: str,
    edits: list[dict],
    parameters: dict[str, Any],
    *,
    action_urn: str,
    actor: Principal,
    at: datetime,
    workspace_id: str = "global",
    reason: str = "",
) -> tuple[dict, dict]:
    """Resolve a static `edits` declaration into property overlays plus
    optional structural rules (create/delete object + link), then write
    both in the same transaction. Property results stay flat
    (`{property: value}`); structural ops are bagged under
    `__structural__` so response splatting and writeback stay compatible.

    An edit sourced from `parameter_name: "reason"` reads the
    invocation's own top-level `reason` — always available, never a
    declared parameter, so recording e.g. `credit_hold_reason` needs no
    parameter of its own to duplicate what the caller already supplied.
    """
    from ..action_structural import (
        STRUCTURAL_KEY,
        apply_structural_edits,
        is_property_edit,
    )

    resolved = {
        edit["property"]: (
            reason
            if edit["source"] == "parameter" and edit["parameter_name"] == "reason"
            else parameters.get(edit["parameter_name"]) if edit["source"] == "parameter" else edit["value"]
        )
        for edit in edits
        if is_property_edit(edit)
    }
    result, prior = await _write_instance_edits(
        conn, tenant_id, object_type, instance_id, resolved, action_urn=action_urn, actor=actor, at=at,
    )
    structural = await apply_structural_edits(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        target_object_type=object_type,
        target_instance_id=instance_id,
        edits=edits,
        parameters=parameters,
        action_urn=action_urn,
        actor=actor,
        at=at,
    )
    if structural.get("links") or structural.get("objects"):
        result = {**result, STRUCTURAL_KEY: structural}
        prior = {**prior, STRUCTURAL_KEY: structural}
    return result, prior


async def _compensate_declarative_action(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    workspace_id: str,
    approval_id: int,
    action_name: str,
    instance_urn: str,
    decider: Principal,
    error: str,
) -> None:
    """Reverts a declarative Action Type's writeback by deleting the
    specific `object_instance_edit` rows this approval wrote. Which
    properties those are is read from the `action_invocation` row
    `approve_action` itself inserted (the most recent one matching this
    `tenant_id`/`action_name`/`instance_urn`) rather than recomputed from
    the Action Type's static `edits` declaration — that list is empty for
    a function-backed Action Type (`edit_function`), whose actual written
    properties are only known at invocation time, so reading them back
    from what was *actually* written is correct for both kinds and
    strictly more precise than recomputing from the definition. Not a
    general undo-stack: `object_instance_edit` is last-write-wins with no
    history, so this reverts to "no edit recorded for these properties,"
    not a restore of whatever value was there before.
    """
    object_type, instance_id = _object_type_and_instance_id_from_instance_urn(instance_urn)
    invocation = await pool.fetchrow(
        "SELECT edits FROM action_invocation WHERE tenant_id = $1 AND action_name = $2 AND instance_urn = $3 "
        "ORDER BY id DESC LIMIT 1",
        tenant_id, action_name, instance_urn,
    )
    edits = json.loads(invocation["edits"]) if invocation and invocation["edits"] else {}
    from ..action_structural import STRUCTURAL_KEY, property_edit_keys, revert_structural

    property_names = property_edit_keys(edits)
    structural = edits.get(STRUCTURAL_KEY) if isinstance(edits, dict) else None

    at = datetime.now(timezone.utc)
    event = _event(
        event_type="knowledge.action.compensated",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        instance_urn=instance_urn,
        actor=decider,
        payload={"action_name": action_name, "instance_urn": instance_urn, "approval_id": approval_id, "error": error},
    )
    async with pool.acquire() as conn:
        async with conn.transaction():
            if property_names:
                await conn.execute(
                    "DELETE FROM object_instance_edit WHERE tenant_id = $1 AND object_type = $2 AND instance_id = $3 "
                    "AND property_name = ANY($4::text[])",
                    tenant_id, object_type, instance_id, property_names,
                )
            if structural:
                await revert_structural(
                    conn,
                    tenant_id=tenant_id,
                    structural=structural,
                    action_urn="compensate",
                    actor=decider,
                    at=at,
                )
            await conn.execute(
                "UPDATE action_approval SET status = 'failed', decision_note = $1 WHERE id = $2",
                f"compensated: {error}", approval_id,
            )
            await outbox.enqueue(conn, event)


async def revert_declarative_action(
    pool: asyncpg.Pool, *, invocation_id: int, tenant_id: str, workspace_id: str, actor: Principal
) -> dict:
    """User-initiated undo of a single already-applied declarative
    invocation — a different concern from `_compensate_declarative_action`
    above (that one reverses a *failed saga's* Step 1, triggered by
    Automation, deletes the overlay rows outright). This restores the
    exact prior state `_apply_declarative_edits` captured, and only ever
    for the single most recent qualifying invocation — Foundry's own
    stated rule, replicated exactly: "cannot be reverted if any
    subsequent edit has been made to that object, even on a different
    property."
    """
    row = await pool.fetchrow(
        "SELECT * FROM action_invocation WHERE id = $1 AND tenant_id = $2", invocation_id, tenant_id
    )
    if row is None:
        raise LookupError(f"action invocation {invocation_id} not found")
    if row["edits"] is None:
        raise ValueError("this invocation made no declarative edits and cannot be reverted")
    if row["reverted_at"] is not None:
        raise ValueError(f"invocation {invocation_id} was already reverted")
    if row["actor_urn"] != actor.urn:
        raise ValueError("only the user who applied this action can revert it")

    from . import _get_action_definition

    definition = await _get_action_definition(pool, tenant_id, row["action_name"])
    if (definition or {}).get("writeback_dataset"):
        raise ValueError("this Action writes back to an external dataset — the external write cannot be undone, so it cannot be reverted")

    later = await pool.fetchval(
        "SELECT 1 FROM action_invocation WHERE tenant_id = $1 AND instance_urn = $2 AND edits IS NOT NULL "
        "AND reverted_at IS NULL AND id > $3 LIMIT 1",
        tenant_id, row["instance_urn"], invocation_id,
    )
    if later:
        raise ValueError("a later action has been applied to this object since — only the most recent edit can be reverted")

    object_type, instance_id = _object_type_and_instance_id_from_instance_urn(row["instance_urn"])
    prior_values = row["prior_values"]
    if isinstance(prior_values, str):
        prior_values = json.loads(prior_values)
    edits_payload = row["edits"]
    if isinstance(edits_payload, str):
        edits_payload = json.loads(edits_payload)

    from ..action_structural import STRUCTURAL_KEY, property_edit_keys, revert_structural

    property_priors = {
        key: prior_values[key]
        for key in property_edit_keys(prior_values)
        if isinstance(prior_values.get(key), dict) and "existed" in prior_values[key]
    }
    structural = None
    if isinstance(edits_payload, dict):
        structural = edits_payload.get(STRUCTURAL_KEY)
    if structural is None and isinstance(prior_values, dict):
        structural = prior_values.get(STRUCTURAL_KEY)

    at = datetime.now(timezone.utc)
    event = _event(
        event_type="knowledge.action.reverted",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        instance_urn=row["instance_urn"],
        actor=actor,
        payload={"action_name": row["action_name"], "instance_urn": row["instance_urn"], "invocation_id": invocation_id},
    )
    async with pool.acquire() as conn:
        async with conn.transaction():
            for property_name, prior in property_priors.items():
                if prior["existed"]:
                    await conn.execute(
                        "UPDATE object_instance_edit SET property_value = $1::jsonb, set_by_action_urn = $2, "
                        "set_by_urn = $3, set_at = $4 WHERE tenant_id = $5 AND object_type = $6 "
                        "AND instance_id = $7 AND property_name = $8",
                        json.dumps(prior["value"]), "revert", actor.urn, at, tenant_id, object_type, instance_id, property_name,
                    )
                else:
                    await conn.execute(
                        "DELETE FROM object_instance_edit WHERE tenant_id = $1 AND object_type = $2 "
                        "AND instance_id = $3 AND property_name = $4",
                        tenant_id, object_type, instance_id, property_name,
                    )
            if structural:
                await revert_structural(
                    conn,
                    tenant_id=tenant_id,
                    structural=structural,
                    action_urn="revert",
                    actor=actor,
                    at=at,
                )
            await conn.execute("UPDATE action_invocation SET reverted_at = $1 WHERE id = $2", at, invocation_id)
            await outbox.enqueue(conn, event)

    return {
        "status": "reverted",
        "invocationId": invocation_id,
        "restoredProperties": list(property_priors.keys()),
        "revertedStructural": bool(structural),
    }


async def _get_unmasked_instance(
    pool: asyncpg.Pool, object_type: str, tenant_id: str, workspace_id: str, instance_id: str
) -> Optional[dict]:
    """The real, unmasked truth for submission-criteria evaluation — a
    business rule about the data, deliberately evaluated without
    `core._resolve_one`'s masking (that's a *read-response* concern; the
    requester's *permission* to invoke the action at all is still gated
    separately, by `_authorize_object_type` in the router, before this
    is ever called).

    Same materialized-then-live-fallback shape `core._resolve_one`
    already uses (a serving-store miss means "not materialized yet", not
    "doesn't exist") — reimplemented narrowly here rather than importing
    `core` at module level, to avoid a module-load-order coupling this
    package doesn't otherwise have (same defensive local-import
    reasoning `request_generic_action` below already uses for
    `ontology`).
    """
    import functools

    from .. import core, ontology, resolver, serving_store
    from pyiceberg.exceptions import NoSuchTableError

    row = await serving_store.get_instance(pool, object_type, tenant_id, instance_id)
    if row is not None:
        return row
    if await serving_store.is_tombstoned(pool, object_type, tenant_id, instance_id):
        return None

    object_type_urn = ontology.object_type_urn(tenant_id, workspace_id, object_type)
    definition = await ontology.get_object_type(pool, object_type_urn)
    if definition is None:
        return None
    dataset_name = definition["source_dataset_urn"].rsplit(":", 1)[-1]
    try:
        rows = await asyncio.to_thread(
            functools.partial(resolver.fetch_generic, dataset_name), id_value=instance_id, **core.ICEBERG_CONFIG
        )
    except NoSuchTableError:
        return None
    return dict(rows[0]) if rows else None


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

    for name, value in parameters.items():
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
