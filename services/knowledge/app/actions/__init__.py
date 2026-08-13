"""`Action` — a named, typed, permissioned and audited
mutation of a business object. Humans, applications, workflows and agents
all go through this same point of passage. Every Action Type is a
self-serve ontology ActionType row (`ontology/action_types.py`'s
registry), applied through the one declarative path (`declarative.py`).

Because a pending high-risk request is not applied, `reject_action` needs
no compensation. But an Action with a `writeback_dataset` set, once
approved, also writes back to that dataset's source system in
Connectivity — a multi-context saga across Knowledge + Connectivity (e.g.
a `closeAccount`-style Action writing `account_closed` back to
`source_erp.customers`).

**Orchestration ownership**: sagas are implemented by the Workflow Engine (the
Automation platform, `services/automation/`), not by whichever service happens to own Step 1.
So this package only ever does **Step 1** — the local mutation, committed
on its own — and publishes `knowledge.action.invoked`. Automation's
`workflow.py` is what's actually listening for that event, calling
Connectivity's write endpoint (Step 2), and — if that fails — calling
back here via `POST /internal/approvals/{id}/compensate` to run
`declarative._compensate_declarative_action`, a second, explicit local
transaction that undoes Step 1.

**Package layout**: `hardcoded.py` keeps the shared `_event` helper;
`declarative.py` owns Action Type apply/compensate; `approval.py`
owns reject/expire/get/list. This `__init__.py` holds schema, shared
`_get_action_definition`/`_apply_now` machinery, and entry points.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

from holon_common import Principal, build_urn, outbox

logger = logging.getLogger("knowledge.actions")

from .approval import (
    APPROVAL_TTL,
    get_approval,
    list_approvals,
    reject_action,
    sweep_expired_approvals,
    sweep_expired_approvals_forever,
)
from .declarative import (
    _apply_declarative_edits,
    _compensate_declarative_action,
    _get_unmasked_instance,
    _object_type_and_instance_id_from_instance_urn,
    _write_instance_edits,
    request_generic_action,
    revert_declarative_action,
)
from .hardcoded import (
    _event,
    WORKFLOW_ENGINE_URN_NAME,
)
from .timeline import list_instance_timeline

__all__ = [
    "ensure_schema",
    "request_generic_action",
    "revert_declarative_action",
    "approve_action",
    "reject_action",
    "compensate_from_workflow_engine",
    "sweep_expired_approvals",
    "sweep_expired_approvals_forever",
    "get_approval",
    "list_approvals",
    "list_instance_timeline",
    "WORKFLOW_ENGINE_URN_NAME",
    "APPROVAL_TTL",
]

DDL = """
CREATE TABLE IF NOT EXISTS action_invocation (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    action_name TEXT NOT NULL,
    instance_urn TEXT NOT NULL,
    actor_urn TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    invoked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Undo/revert support — additive. `edits` is `{property: newValue}`;
-- `prior_values` is `{property: {"existed": bool, "value": ...}}`.
ALTER TABLE action_invocation ADD COLUMN IF NOT EXISTS edits JSONB;
ALTER TABLE action_invocation ADD COLUMN IF NOT EXISTS prior_values JSONB;
ALTER TABLE action_invocation ADD COLUMN IF NOT EXISTS reverted_at TIMESTAMPTZ;

-- Legacy overlay tables retained for existing DBs; new Actions use
-- `object_instance_edit` exclusively.
CREATE TABLE IF NOT EXISTS customer_credit_hold (
    customer_id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    on_hold BOOLEAN NOT NULL,
    reason TEXT NOT NULL,
    set_by_urn TEXT NOT NULL,
    set_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customer_account_status (
    customer_id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    closed BOOLEAN NOT NULL,
    reason TEXT NOT NULL,
    set_by_urn TEXT NOT NULL,
    set_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS action_approval (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    action_name TEXT NOT NULL,
    instance_urn TEXT NOT NULL,
    requested_by_urn TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '24 hours'),
    decided_by_urn TEXT,
    decided_at TIMESTAMPTZ,
    decision_note TEXT
);

ALTER TABLE action_approval ADD COLUMN IF NOT EXISTS parameters JSONB NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS saga_execution (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    approval_id BIGINT NOT NULL,
    action_name TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS object_instance_edit (
    tenant_id TEXT NOT NULL,
    object_type TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    property_name TEXT NOT NULL,
    property_value JSONB NOT NULL,
    set_by_action_urn TEXT NOT NULL,
    set_by_urn TEXT NOT NULL,
    set_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, object_type, instance_id, property_name)
);
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    from ..action_structural import TOMBSTONE_DDL

    await conn.execute(DDL)
    await conn.execute(TOMBSTONE_DDL)


async def _get_action_definition(pool: asyncpg.Pool, tenant_id: str, action_name: str) -> Optional[dict]:
    """Resolve an Action from the ontology `action_type` registry into the
    adapted shape (`target_object_type`/`required_permission`/`risk_level`/
    …) used by readers and apply paths. `_declarative` carries the full
    registry row for the apply path.
    """
    from .. import ontology

    action_type = await ontology.get_action_type(pool, tenant_id, action_name)
    if action_type is None:
        return None
    return {
        "target_object_type": action_type["target_object_type"],
        "target_interface": action_type.get("target_interface"),
        "required_permission": action_type["required_permission"],
        "risk_level": action_type["risk_level"],
        "description": action_type["description"],
        "function_side_effect": action_type.get("function_side_effect"),
        "writeback_dataset": action_type.get("writeback_dataset"),
        "edit_function": action_type.get("edit_function"),
        "parameters": action_type["parameters"],
        "edits": action_type["edits"],
        "type_classes": action_type.get("type_classes") or [],
        "_declarative": action_type,
    }


async def _invoke_function_side_effect(
    pool: asyncpg.Pool, *, tenant_id: str, workspace_id: str, action_name: str, instance_id
) -> None:
    """A Function side effect is optional operational enrichment, never a
    consistency-critical step — always runs *after* `_apply_now`'s own
    transaction, best-effort.
    """
    definition = await _get_action_definition(pool, tenant_id, action_name)
    function_name = (definition or {}).get("function_side_effect")
    if function_name is None:
        return

    from .. import function_registry, ontology, serving_store

    try:
        registration = await function_registry.find_active_function_by_name(pool, function_name)
        if registration is None:
            logger.warning(
                "action %s's function_side_effect %r is not a registered, active Function plugin — skipping",
                action_name, function_name,
            )
            return
        target_object_type = definition["target_object_type"]
        object_type_urn = ontology.object_type_urn(tenant_id, workspace_id, target_object_type)
        object_type = await ontology.get_object_type(pool, object_type_urn)
        row = await serving_store.get_instance(pool, target_object_type, tenant_id, instance_id)
        if object_type is None or row is None:
            return
        translated = {camel: row.get(source_col) for camel, source_col in object_type["property_mapping"].items()}
        plugin = function_registry.load_function_plugin(registration["manifest"])
        result = await plugin.call(**translated)
        logger.info(
            "action %s's function_side_effect %r produced %r for %s/%s",
            action_name, function_name, result, target_object_type, instance_id,
        )
    except Exception:
        logger.exception(
            "action %s's function_side_effect %r failed for instance %s — the action itself already "
            "committed and is unaffected",
            action_name, function_name, instance_id,
        )


async def _resolve_function_backed_edits(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    workspace_id: str,
    action_name: str,
    definition: dict,
    object_type: str,
    instance_id: str,
    parameters: dict[str, Any],
) -> dict:
    """A function-backed Action Type (`edit_function` instead of a static
    `edits` list) has the named Function plugin's return value BECOME the
    applied edits. Unlike `_invoke_function_side_effect` this *raises*
    rather than logging-and-skipping.
    """
    from .. import function_registry, ontology

    action_type = definition["_declarative"]
    function_name = action_type["edit_function"]
    registration = await function_registry.find_active_function_by_name(pool, function_name)
    if registration is None:
        raise ValueError(f"{action_name}'s edit_function {function_name!r} is not a registered, active Function plugin")

    object_type_urn = ontology.object_type_urn(tenant_id, workspace_id, object_type)
    object_type_def = await ontology.get_object_type(pool, object_type_urn)
    if object_type_def is None:
        raise LookupError(f"{object_type} not found")
    instance_row = await _get_unmasked_instance(pool, object_type, tenant_id, workspace_id, instance_id)
    if instance_row is None:
        raise LookupError(f"{object_type}/{instance_id} not found")

    translated = {camel: instance_row.get(source_col) for camel, source_col in object_type_def["property_mapping"].items()}
    plugin = function_registry.load_function_plugin(registration["manifest"])
    output = await plugin.call(**{**translated, **parameters})
    if not isinstance(output, dict) or not output:
        raise ValueError(
            f"{action_name}'s edit_function {function_name!r} must return a non-empty {{property: value}} "
            f"dict of edits, got {output!r}"
        )
    target_interface = action_type.get("target_interface")
    if target_interface:
        interface = await ontology.get_interface_type(pool, tenant_id, target_interface)
        allowed_properties = set((interface or {}).get("required_properties") or [])
        for property_name in output:
            if property_name not in allowed_properties:
                raise ValueError(
                    f"{action_name} is scoped to interface {target_interface!r} and cannot edit "
                    f"{property_name!r} — only the interface's required_properties are allowed"
                )

    property_types = object_type_def.get("property_types") or {}
    for property_name, value in output.items():
        rule = property_types.get(property_name)
        if rule is None:
            continue
        if rule.get("editable") is False:
            raise ValueError(f"property {property_name!r} is not editable")
        if rule.get("required") and value is None:
            raise ValueError(f"property {property_name!r} is required and cannot be set to null")
        if value is not None and rule.get("kind") in ("value_type", "shared_property_type", "struct", "array"):
            type_error = await ontology.validate_typed_property_value(
                pool, tenant_id, rule, value, property_name=property_name
            )
            if type_error is not None:
                raise ValueError(type_error)

    return output


async def _apply_now(
    pool: asyncpg.Pool,
    action_name: str,
    tenant_id: str,
    workspace_id: str,
    instance_urn: str,
    customer_id: Optional[int],
    actor: Principal,
    reason: str,
    *,
    object_type: Optional[str] = None,
    instance_id: Optional[str] = None,
    parameters: Optional[dict] = None,
) -> dict:
    at = datetime.now(timezone.utc)
    event = _event(
        event_type="knowledge.action.invoked",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        instance_urn=instance_urn,
        actor=actor,
        payload={"action_name": action_name, "instance_urn": instance_urn, "reason": reason},
    )
    edits_for_invocation: Optional[dict] = None
    prior_for_invocation: Optional[dict] = None

    definition = await _get_action_definition(pool, tenant_id, action_name)
    if definition is None:
        raise LookupError(f"unknown Action: {action_name}")
    resolved_function_edits: Optional[dict] = None
    if definition["_declarative"].get("edit_function"):
        resolved_function_edits = await _resolve_function_backed_edits(
            pool, tenant_id=tenant_id, workspace_id=workspace_id, action_name=action_name,
            definition=definition, object_type=object_type, instance_id=instance_id,
            parameters=parameters or {},
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            action_urn = build_urn(tenant_id, "global", "action-type", action_name)
            if resolved_function_edits is not None:
                result, prior = await _write_instance_edits(
                    conn, tenant_id, object_type, instance_id, resolved_function_edits,
                    action_urn=action_urn, actor=actor, at=at,
                )
            else:
                result, prior = await _apply_declarative_edits(
                    conn, tenant_id, object_type, instance_id, definition["_declarative"]["edits"],
                    parameters or {}, action_urn=action_urn, actor=actor, at=at, workspace_id=workspace_id,
                    reason=reason,
                )
            edits_for_invocation = result
            prior_for_invocation = prior
            invocation_id = await conn.fetchval(
                "INSERT INTO action_invocation (tenant_id, action_name, instance_urn, actor_urn, actor_type, reason, edits, prior_values) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb) RETURNING id",
                tenant_id, action_name, instance_urn, actor.urn, actor.type, reason,
                json.dumps(edits_for_invocation) if edits_for_invocation is not None else None,
                json.dumps(prior_for_invocation) if prior_for_invocation is not None else None,
            )
            await outbox.enqueue(conn, event)

    await _invoke_function_side_effect(
        pool, tenant_id=tenant_id, workspace_id=workspace_id, action_name=action_name,
        instance_id=instance_id if instance_id is not None else customer_id,
    )

    from .metrics import ACTION_EVENTS
    from .notify import deliver_action_notification

    ACTION_EVENTS.labels("applied", action_name).inc()
    try:
        webhook = definition["_declarative"].get("notify_webhook")
        await deliver_action_notification(
            webhook_url=webhook,
            event="knowledge.action.invoked",
            payload={
                "action_name": action_name,
                "instance_urn": instance_urn,
                "tenant_id": tenant_id,
                "status": "applied",
                "invocation_id": invocation_id,
            },
        )
    except Exception:
        logger.exception("action notification delivery raised unexpectedly")

    from ..action_structural import split_result_for_response

    return {
        "status": "applied",
        "action": action_name,
        "riskLevel": definition["risk_level"],
        "invocationId": invocation_id,
        **split_result_for_response(result or {}),
    }


async def approve_action(
    pool: asyncpg.Pool,
    *,
    approval_id: int,
    workspace_id: str,
    decider: Principal,
    note: Optional[str] = None,
) -> dict:
    preview = await pool.fetchrow("SELECT * FROM action_approval WHERE id = $1", approval_id)
    if preview is None:
        raise LookupError(f"approval {approval_id} not found")

    definition = await _get_action_definition(pool, preview["tenant_id"], preview["action_name"])
    if definition is None:
        raise LookupError(f"unknown Action: {preview['action_name']}")
    resolved_function_edits: Optional[dict] = None
    if definition["_declarative"].get("edit_function"):
        preview_object_type, preview_instance_id = _object_type_and_instance_id_from_instance_urn(preview["instance_urn"])
        preview_parameters = preview["parameters"]
        if isinstance(preview_parameters, str):
            preview_parameters = json.loads(preview_parameters)
        resolved_function_edits = await _resolve_function_backed_edits(
            pool, tenant_id=preview["tenant_id"], workspace_id=workspace_id, action_name=preview["action_name"],
            definition=definition, object_type=preview_object_type, instance_id=preview_instance_id,
            parameters=preview_parameters or {},
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM action_approval WHERE id = $1 FOR UPDATE", approval_id)
            if row is None:
                raise LookupError(f"approval {approval_id} not found")
            if row["status"] != "pending":
                raise ValueError(f"approval {approval_id} is already {row['status']}")
            if row["expires_at"] < datetime.now(timezone.utc):
                raise ValueError(f"approval {approval_id} has expired")

            action_name = row["action_name"]
            tenant_id = row["tenant_id"]
            instance_urn = row["instance_urn"]
            reason = row["reason"]
            at = datetime.now(timezone.utc)

            writeback_edits: Optional[dict] = None
            object_type, instance_id = _object_type_and_instance_id_from_instance_urn(instance_urn)
            action_urn = build_urn(tenant_id, "global", "action-type", action_name)
            if resolved_function_edits is not None:
                result, prior = await _write_instance_edits(
                    conn, tenant_id, object_type, instance_id, resolved_function_edits,
                    action_urn=action_urn, actor=decider, at=at,
                )
            else:
                parameters = row["parameters"]
                if isinstance(parameters, str):
                    parameters = json.loads(parameters)
                result, prior = await _apply_declarative_edits(
                    conn, tenant_id, object_type, instance_id, definition["_declarative"]["edits"],
                    parameters, action_urn=action_urn, actor=decider, at=at, workspace_id=workspace_id,
                    reason=reason,
                )
            edits_for_invocation = result
            prior_for_invocation = prior
            if definition["_declarative"].get("writeback_dataset"):
                from ..action_structural import split_result_for_response

                writeback_edits = split_result_for_response(result)

            await conn.execute(
                "UPDATE action_approval SET status = 'approved', decided_by_urn = $1, decided_at = $2, decision_note = $3 WHERE id = $4",
                decider.urn, at, note, approval_id,
            )
            invocation_id = await conn.fetchval(
                "INSERT INTO action_invocation (tenant_id, action_name, instance_urn, actor_urn, actor_type, reason, edits, prior_values) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb) RETURNING id",
                tenant_id, action_name, instance_urn, decider.urn, decider.type, reason,
                json.dumps(edits_for_invocation) if edits_for_invocation is not None else None,
                json.dumps(prior_for_invocation) if prior_for_invocation is not None else None,
            )
            event_payload = {"action_name": action_name, "instance_urn": instance_urn, "reason": reason, "approval_id": approval_id}
            if writeback_edits is not None:
                event_payload["edits"] = writeback_edits
            event = _event(
                event_type="knowledge.action.invoked",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                instance_urn=instance_urn,
                actor=decider,
                payload=event_payload,
            )
            await outbox.enqueue(conn, event)

    saga_status = "processing" if writeback_edits is not None else "completed"
    from ..action_structural import split_result_for_response

    return {
        "status": "approved", "approvalId": approval_id, "action": action_name, "sagaStatus": saga_status,
        "invocationId": invocation_id, **split_result_for_response(result or {}),
    }


async def compensate_from_workflow_engine(pool: asyncpg.Pool, *, approval_id: int, workspace_id: str, error: str) -> dict:
    """Entry point for `POST /internal/approvals/{id}/compensate` — the
    callback Automation's Workflow Engine makes when its own Step 2 fails.
    """
    row = await pool.fetchrow("SELECT * FROM action_approval WHERE id = $1", approval_id)
    if row is None:
        raise LookupError(f"approval {approval_id} not found")
    if row["status"] != "approved":
        raise ValueError(f"approval {approval_id} is {row['status']}, not approved — nothing to compensate")

    instance_urn = row["instance_urn"]
    decider = Principal(
        urn=build_urn(row["tenant_id"], "global", "service-account", WORKFLOW_ENGINE_URN_NAME),
        type="service_account",
        tenant_id=row["tenant_id"],
        display_name="Automation Workflow Engine",
    )
    await _compensate_declarative_action(
        pool,
        tenant_id=row["tenant_id"],
        workspace_id=workspace_id,
        approval_id=approval_id,
        action_name=row["action_name"],
        instance_urn=instance_urn,
        decider=decider,
        error=error,
    )
    return {"approvalId": approval_id, "status": "compensated"}
