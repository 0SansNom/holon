"""Action Type registry — the no-code counterpart to `actions.py`'s
`ACTION_DEFINITIONS` + `register_apply_function`. A hardcoded Action
needs Python written and deployed; an Action Type is pure
configuration: named parameters (each a `value_type` reference,
validated at invocation), a list of declarative `edits` (`set` a
property, from a parameter or a literal — deliberately no relation
link/unlink, no create/delete-object, a stated scope boundary), and
`submission_criteria` (simple comparisons against the target instance's
current state, evaluated before anything is requested or applied).

A simple upsert registry (like Interfaces/Markings/RelationTypes), not
versioned/branched like ObjectTypes — an Action Type's definition is
lower-stakes to iterate on than a schema, and every other registry in
this build already treats "definition" and "governed schema lifecycle"
as two different tiers.

`actions.py` is what actually *applies* an Action Type at request/apply
time (`_get_action_definition`'s fallback, `_apply_declarative_edits`,
`request_generic_action`) — this module only owns the registry.
"""

from __future__ import annotations

import json
from typing import Optional

import asyncpg

_ALLOWED_OPERATORS = {"eq", "neq", "gt", "gte", "lt", "lte"}
_ALLOWED_RISK_LEVELS = {"low", "high"}
_ALLOWED_EDIT_SOURCES = {"parameter", "literal"}

# `actions.py`'s `_apply_now`/`approve_action` splat a declarative
# Action's edit results (`{property: newValue}`) straight into their own
# top-level response dict (`{"status": "applied", "action": ..., **result}`)
# — the exact same flat shape the two hardcoded Actions' own
# `_apply_put_on_credit_hold`/`_apply_close_account` already return, kept
# consistent on purpose. An edit named e.g. "status" would silently
# overwrite the response's own "status" control field via that splat —
# rejected here, at registration, rather than corrupting a response
# later — the same "validate at definition time" posture every other
# structural check in this module already takes.
_RESERVED_RESPONSE_KEYS = {"status", "action", "riskLevel", "approvalId", "sagaStatus"}


async def create_action_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    target_object_type: str,
    required_permission: str,
    risk_level: str,
    description: str,
    parameters: list[dict],
    edits: list[dict],
    submission_criteria: Optional[list[dict]] = None,
    function_side_effect: Optional[str] = None,
    writeback_dataset: Optional[str] = None,
) -> dict:
    """Structural validation only — real references (a parameter's
    `value_type`, an edit's target property, `writeback_dataset` naming
    a real Connectivity `write_target`) are checked against live state
    at invocation time (`actions.request_generic_action`), not here: an
    Action Type may be registered before the ObjectType — or the write
    target — it references even exists yet, same "define now, validate
    against real state at the point of use" posture `interface_type`'s
    `required_properties` already has.

    `writeback_dataset` requires `risk_level == "high"` — the saga that
    actually performs a writeback (Automation's `consume_events`) only
    ever triggers for an *approved* invocation (`approval_id is not
    None`); a low-risk Action never creates one, so declaring a
    writeback target on one would silently never fire. Rejected here
    rather than accepted and quietly ignored.
    """
    if risk_level not in _ALLOWED_RISK_LEVELS:
        raise ValueError(f"unknown risk_level: {risk_level!r} (expected one of {sorted(_ALLOWED_RISK_LEVELS)})")
    if not description:
        raise ValueError("description is required")
    if not edits:
        raise ValueError("an Action Type needs at least one edit")
    if writeback_dataset is not None and risk_level != "high":
        raise ValueError("writeback_dataset requires risk_level='high' — a low-risk Action's saga never triggers")

    parameter_names = set()
    for parameter in parameters:
        if "name" not in parameter or "value_type" not in parameter:
            raise ValueError(f"malformed parameter declaration: {parameter!r} (expected 'name' and 'value_type')")
        parameter_names.add(parameter["name"])

    for edit in edits:
        if "property" not in edit or "source" not in edit:
            raise ValueError(f"malformed edit: {edit!r} (expected 'property' and 'source')")
        if edit["property"] in _RESERVED_RESPONSE_KEYS:
            raise ValueError(
                f"edit property {edit['property']!r} collides with a reserved response field "
                f"(one of {sorted(_RESERVED_RESPONSE_KEYS)}) — rename the property"
            )
        if edit["source"] not in _ALLOWED_EDIT_SOURCES:
            raise ValueError(f"edit for {edit['property']!r} has unknown source {edit['source']!r} (expected one of {sorted(_ALLOWED_EDIT_SOURCES)})")
        if edit["source"] == "parameter":
            if edit.get("parameter_name") not in parameter_names:
                raise ValueError(f"edit for {edit['property']!r} references undeclared parameter {edit.get('parameter_name')!r}")
        elif "value" not in edit:
            raise ValueError(f"edit for {edit['property']!r}: source='literal' requires a 'value'")

    for criterion in submission_criteria or []:
        if criterion.get("operator") not in _ALLOWED_OPERATORS:
            raise ValueError(
                f"submission criterion for {criterion.get('property')!r} has unknown operator "
                f"{criterion.get('operator')!r} (expected one of {sorted(_ALLOWED_OPERATORS)})"
            )
        if "property" not in criterion or "value" not in criterion:
            raise ValueError(f"malformed submission criterion: {criterion!r} (expected 'property', 'operator', 'value')")

    await pool.execute(
        """
        INSERT INTO action_type
            (tenant_id, name, target_object_type, required_permission, risk_level, description,
             parameters, edits, submission_criteria, function_side_effect, writeback_dataset)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9::jsonb, $10, $11)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            target_object_type = EXCLUDED.target_object_type,
            required_permission = EXCLUDED.required_permission,
            risk_level = EXCLUDED.risk_level,
            description = EXCLUDED.description,
            parameters = EXCLUDED.parameters,
            edits = EXCLUDED.edits,
            submission_criteria = EXCLUDED.submission_criteria,
            function_side_effect = EXCLUDED.function_side_effect,
            writeback_dataset = EXCLUDED.writeback_dataset
        """,
        tenant_id, name, target_object_type, required_permission, risk_level, description,
        json.dumps(parameters), json.dumps(edits), json.dumps(submission_criteria or []), function_side_effect,
        writeback_dataset,
    )
    return await get_action_type(pool, tenant_id, name)


def _parse_action_type_row(row: asyncpg.Record) -> dict:
    result = dict(row)
    for key in ("parameters", "edits", "submission_criteria"):
        if isinstance(result[key], str):
            result[key] = json.loads(result[key])
    return result


async def get_action_type(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM action_type WHERE tenant_id = $1 AND name = $2", tenant_id, name)
    return _parse_action_type_row(row) if row else None


async def list_action_types(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM action_type WHERE tenant_id = $1 ORDER BY name", tenant_id)
    return [_parse_action_type_row(row) for row in rows]
