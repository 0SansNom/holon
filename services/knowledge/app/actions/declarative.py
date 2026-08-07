"""Declarative Action Types (`ontology/action_types.py`'s registry) —
the no-code counterpart to `hardcoded.py`'s two Python functions. A
leaf module: imports `_event` from `.hardcoded` (one-way — this file
depends on that one, never the reverse) plus package-external modules
(`ontology`, `core`, `resolver`, `serving_store`), never from
`__init__.py`. `__init__.py`'s shared orchestration (`_apply_now`/
`approve_action`) imports `_apply_declarative_edits`/
`_compensate_declarative_action` from here, dispatching into them
whenever an action name isn't in `hardcoded.ACTION_DEFINITIONS`.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import asyncpg

from holon_common import Principal, build_urn, outbox

from .hardcoded import _event

_OPERATORS = {
    "eq": lambda actual, expected: actual == expected,
    "neq": lambda actual, expected: actual != expected,
    "gt": lambda actual, expected: actual is not None and actual > expected,
    "gte": lambda actual, expected: actual is not None and actual >= expected,
    "lt": lambda actual, expected: actual is not None and actual < expected,
    "lte": lambda actual, expected: actual is not None and actual <= expected,
}


def _object_type_and_instance_id_from_instance_urn(instance_urn: str) -> tuple[str, str]:
    """Generic counterpart to `hardcoded._customer_id_from_instance_urn`
    — every `instance_urn` this package ever builds (`request_action`,
    `request_generic_action`) already has the `{ObjectType}/{id}` shape
    in its final segment; this just splits it instead of assuming it's
    always `Customer/{int}`.
    """
    local = instance_urn.rsplit(":", 1)[-1]
    object_type, instance_id = local.split("/", 1)
    return object_type, instance_id


def _evaluate_criteria(instance_row: dict, criteria: list[dict]) -> Optional[str]:
    """Pure function, fixed and closed operator set — never `eval()`.
    Evaluated against the instance's real, unmasked state
    (`serving_store.get_instance`, called by `request_generic_action`
    before this): a submission criterion is a business rule about the
    data itself, a different concern from the requester's *permission*
    to invoke the action at all, which ABAC/ReBAC still gates separately
    and unchanged. Returns `None` if every criterion passes, otherwise
    the first violation's message.
    """
    for criterion in criteria:
        property_name = criterion["property"]
        operator = criterion["operator"]
        expected = criterion["value"]
        actual = instance_row.get(property_name)
        try:
            passed = _OPERATORS[operator](actual, expected)
        except TypeError:
            # e.g. `gt` between a string and a number — not comparable,
            # so the criterion cannot have been satisfied.
            passed = False
        if not passed:
            return f"submission criterion failed: {property_name} {operator} {expected!r} (actual: {actual!r})"
    return None


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
) -> dict:
    """The generic counterpart to `hardcoded._apply_put_on_credit_hold`/
    `_apply_close_account` — writes into `object_instance_edit` (one row
    per property, upsert) instead of a bespoke per-Action table, so a
    *new* declarative Action Type never needs a new table. Returns
    `{property: newValue, ...}`, the same shape the two hardcoded apply
    functions already return, so `__init__.py`'s `_apply_now` flow
    (event payload, `action_invocation` insert) needs no changes.
    """
    result: dict[str, Any] = {}
    for edit in edits:
        property_name = edit["property"]
        value = parameters.get(edit["parameter_name"]) if edit["source"] == "parameter" else edit["value"]
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
    return result


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
    """The generic counterpart to `hardcoded._compensate_close_account` —
    reverts a declarative Action Type's writeback by deleting the
    specific `object_instance_edit` rows this approval wrote, identified
    by the Action Type's own declared `edits` (a fixed set of property
    names, the same list `_apply_declarative_edits` used to write them —
    recomputed here rather than stored, since it's definitional, not
    data-dependent). Not a general undo-stack: `object_instance_edit` is
    last-write-wins with no history, so this reverts to "no edit
    recorded for these properties," the same "known, fixed reversion"
    honesty `_compensate_close_account` already has for its own overlay
    (that one doesn't restore a prior `customer_account_status` row
    either — there isn't one to restore).
    """
    from . import _get_action_definition

    object_type, instance_id = _object_type_and_instance_id_from_instance_urn(instance_urn)
    definition = await _get_action_definition(pool, tenant_id, action_name)
    property_names = [edit["property"] for edit in definition["_declarative"]["edits"]]

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
            await conn.execute(
                "DELETE FROM object_instance_edit WHERE tenant_id = $1 AND object_type = $2 AND instance_id = $3 "
                "AND property_name = ANY($4::text[])",
                tenant_id, object_type, instance_id, property_names,
            )
            await conn.execute(
                "UPDATE action_approval SET status = 'failed', decision_note = $1 WHERE id = $2",
                f"compensated: {error}", approval_id,
            )
            await outbox.enqueue(conn, event)


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
    """The generic entry point for a declarative Action Type — parallel
    to `hardcoded`'s implicit `request_action` (Customer-only, untouched)
    rather than a replacement, since that one's signature is baked into
    every existing caller. Validates every declared parameter against
    its Value Type (real format enforcement, not just a declared schema)
    and every submission criterion against the instance's actual current
    state, *before* creating any approval or applying anything — a
    failure here is a clean 400, never a partially-applied write.
    """
    from .. import ontology
    from . import APPROVAL_TTL, _apply_now

    action_type = await ontology.get_action_type(pool, tenant_id, action_name)
    if action_type is None:
        raise LookupError(f"unknown Action Type: {action_name}")
    if action_type["target_object_type"] != object_type:
        raise ValueError(f"{action_name} targets {action_type['target_object_type']!r}, not {object_type!r}")

    declared_parameters = {p["name"]: p for p in action_type["parameters"]}
    for name, declaration in declared_parameters.items():
        if declaration.get("required", True) and name not in parameters:
            raise ValueError(f"missing required parameter: {name!r}")
    for name, value in parameters.items():
        declaration = declared_parameters.get(name)
        if declaration is None:
            raise ValueError(f"unknown parameter: {name!r}")
        value_type = await ontology.get_value_type(pool, tenant_id, declaration["value_type"])
        if value_type is None:
            raise ValueError(f"parameter {name!r} references unknown value_type {declaration['value_type']!r}")
        error = ontology.validate_value(value, value_type)
        if error is not None:
            raise ValueError(f"parameter {name!r}: {error}")

    instance_row = await _get_unmasked_instance(pool, object_type, tenant_id, workspace_id, instance_id)
    if instance_row is None:
        raise LookupError(f"{object_type}/{instance_id} not found")
    criteria_error = _evaluate_criteria(instance_row, action_type["submission_criteria"])
    if criteria_error is not None:
        raise ValueError(criteria_error)

    instance_urn = build_urn(tenant_id, workspace_id, "instance", f"{object_type}/{instance_id}")

    if action_type["risk_level"] == "low":
        return await _apply_now(
            pool, action_name, tenant_id, workspace_id, instance_urn, None, principal, reason,
            object_type=object_type, instance_id=instance_id, parameters=parameters,
        )

    # High risk: propose only, same shape `hardcoded`'s implicit
    # `request_action` already uses — `parameters` persisted alongside
    # so `approve_action` can apply the right `edits` once a decision
    # actually comes in.
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

    return {
        "status": "pending_approval",
        "approvalId": approval_id,
        "action": action_name,
        "riskLevel": action_type["risk_level"],
        "expiresAt": expires_at.isoformat(),
    }
