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
) -> tuple[dict, dict]:
    """The shared upsert-into-`object_instance_edit` + prior-value-capture
    core, factored out of `_apply_declarative_edits` so a function-backed
    Action Type (`__init__._resolve_function_backed_edits`) can write its
    *already-resolved* `{property: value}` dict through the exact same
    path a static `edits` list uses — one row per property, upsert,
    instead of a bespoke per-Action table. Returns `(result, prior)`:
    `result` is `{property: newValue, ...}`, the same shape the two
    hardcoded apply functions already return; `prior` is
    `{property: {"existed": bool, "value": ...}}`, captured before each
    upsert — `__init__.py` persists it onto `action_invocation` so
    `revert_declarative_action` below can restore it later. `existed`
    disambiguates "never set before" from "explicitly set to JSON null",
    which a bare prior value could never do on its own.
    """
    result: dict[str, Any] = {}
    prior: dict[str, Any] = {}
    for property_name, value in resolved.items():
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
) -> tuple[dict, dict]:
    """The generic counterpart to `hardcoded._apply_put_on_credit_hold`/
    `_apply_close_account` — resolves a static `edits` declaration
    (`source: "parameter"|"literal"`) into a flat `{property: value}`
    dict, then writes it via `_write_instance_edits`.
    """
    resolved = {
        edit["property"]: (parameters.get(edit["parameter_name"]) if edit["source"] == "parameter" else edit["value"])
        for edit in edits
    }
    return await _write_instance_edits(
        conn, tenant_id, object_type, instance_id, resolved, action_urn=action_urn, actor=actor, at=at,
    )


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
    the same "known, fixed reversion" honesty `_compensate_close_account`
    already has for its own overlay (that one doesn't restore a prior
    `customer_account_status` row either — there isn't one to restore).
    """
    object_type, instance_id = _object_type_and_instance_id_from_instance_urn(instance_urn)
    invocation = await pool.fetchrow(
        "SELECT edits FROM action_invocation WHERE tenant_id = $1 AND action_name = $2 AND instance_urn = $3 "
        "ORDER BY id DESC LIMIT 1",
        tenant_id, action_name, instance_urn,
    )
    edits = json.loads(invocation["edits"]) if invocation and invocation["edits"] else {}
    property_names = list(edits.keys())

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
            for property_name, prior in prior_values.items():
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
            await conn.execute("UPDATE action_invocation SET reverted_at = $1 WHERE id = $2", at, invocation_id)
            await outbox.enqueue(conn, event)

    return {"status": "reverted", "invocationId": invocation_id, "restoredProperties": list(prior_values.keys())}


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

    # Resolved once, reused below both for the target check (ObjectType-
    # or Interface-scoped) and the property-control check further down —
    # previously fetched twice.
    object_type_urn = ontology.object_type_urn(tenant_id, workspace_id, object_type)
    definition = await ontology.get_object_type(pool, object_type_urn)

    target_interface = action_type.get("target_interface")
    if target_interface:
        # Actions on interfaces: invocable against any ObjectType that
        # currently `implements` this interface — same generalization
        # Foundry's own "interface action rules" apply, restricted the
        # same way: only the interface's own `required_properties` may
        # ever be edited, never a type-specific one, checked against
        # every declared edit (not just the ones this call happens to
        # touch) since the restriction is a property of the Action Type
        # itself, not of any one invocation.
        if target_interface not in ((definition or {}).get("implements") or []):
            raise ValueError(f"{action_name} targets interface {target_interface!r}, which {object_type!r} does not implement")
        interface = await ontology.get_interface_type(pool, tenant_id, target_interface)
        allowed_properties = set((interface or {}).get("required_properties") or [])
        for edit in action_type["edits"]:
            if edit["property"] not in allowed_properties:
                raise ValueError(
                    f"{action_name} is scoped to interface {target_interface!r} and cannot edit "
                    f"{edit['property']!r} — only the interface's required_properties are allowed"
                )
    elif action_type["target_object_type"] != object_type:
        raise ValueError(f"{action_name} targets {action_type['target_object_type']!r}, not {object_type!r}")

    declared_parameters = {p["name"]: p for p in action_type["parameters"]}
    for name, declaration in declared_parameters.items():
        if declaration.get("required", True) and name not in parameters:
            raise ValueError(f"missing required parameter: {name!r}")
    for name, value in parameters.items():
        declaration = declared_parameters.get(name)
        if declaration is None:
            raise ValueError(f"unknown parameter: {name!r}")
        if declaration.get("kind", "value_type") == "object_reference":
            referenced_type = declaration["object_type"]
            referenced_instance = await _get_unmasked_instance(pool, referenced_type, tenant_id, workspace_id, str(value))
            if referenced_instance is None:
                raise ValueError(f"parameter {name!r}: {referenced_type}/{value} does not exist")
            continue
        value_type = await ontology.get_value_type(pool, tenant_id, declaration["value_type"])
        if value_type is None:
            raise ValueError(f"parameter {name!r} references unknown value_type {declaration['value_type']!r}")
        error = ontology.validate_value(value, value_type)
        if error is not None:
            raise ValueError(f"parameter {name!r}: {error}")

    # Property control: an edit may target a property the ObjectType has
    # declared non-editable, or may null out one declared required — both
    # real teeth on `property_types`' `editable`/`required` flags, checked
    # here (against the edits this Action Type will actually perform)
    # rather than at Action Type registration time, since the target
    # ObjectType's `property_types` is live state, same "structural now,
    # real references at invocation" split `value_type` above already
    # follows. A property with no `property_types` entry is unaffected —
    # every Action Type predating this feature keeps working. `definition`
    # already resolved above for the target check.
    property_types = (definition or {}).get("property_types") or {}
    for edit in action_type["edits"]:
        rule = property_types.get(edit["property"])
        if rule is None:
            continue
        edit_value = parameters.get(edit["parameter_name"]) if edit["source"] == "parameter" else edit["value"]
        if rule.get("editable") is False:
            raise ValueError(f"property {edit['property']!r} is not editable")
        if rule.get("required") and edit_value is None:
            raise ValueError(f"property {edit['property']!r} is required and cannot be set to null")

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
