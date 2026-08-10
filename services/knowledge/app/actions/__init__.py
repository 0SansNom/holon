"""`Action` — a named, typed, permissioned and audited
mutation of a business object. Humans, applications, workflows and agents
all go through this same point of passage.

Two hardcoded Actions exist (`hardcoded.py`), chosen to make `risk_level`
actually branch behavior:

- `Customer.putOnCreditHold` — `risk_level="low"`. Low risk (no external-source write,
  no global-object mutation, no deletion, no spend threshold), so it applies immediately.
- `Customer.closeAccount` — `risk_level="high"` (deletion-class). Requires explicit
  human approval before it applies: `request_action` only *proposes* it (an `action_approval`
  row, status `pending`); the mutation happens in `approve_action`, gated by the `approve` SpiceDB
  permission (workspace `admin` only — a strictly smaller set than `editor`, so the
  requester can never approve their own request).

Because a pending high-risk request is not applied, `reject_action` needs
no compensation. But `closeAccount`, once approved, also writes
`account_closed` back to `source_erp.customers` in Connectivity. That makes it
a multi-context saga across Knowledge + Connectivity. Any declarative
Action Type (`declarative.py`, `ontology/action_types.py`'s registry)
with a `writeback_dataset` set follows the identical saga shape, generalized.

**Orchestration ownership**: sagas are implemented by the Workflow Engine (the
Automation platform, `services/automation/`), not by whichever service happens to own Step 1.
So this package only ever does **Step 1** — the local mutation, committed
on its own — and publishes `knowledge.action.invoked`. Automation's
`workflow.py` is what's actually listening for that event, calling
Connectivity's write endpoint (Step 2), and — if that fails — calling
back here via `POST /internal/approvals/{id}/compensate` to run
`hardcoded._compensate_close_account`/`declarative._compensate_declarative_action`,
a second, explicit local transaction that undoes Step 1.
`putOnCreditHold` has no external step, so it's never even in
`hardcoded.WORKFLOW_DELEGATED_ACTIONS`.

**Package layout**: split from a single ~1000-line `actions.py` into
`hardcoded.py` (the two Customer Actions, a leaf module) and
`declarative.py` (the Action Type registry's apply/compensate, a leaf
module that imports `_event` from `.hardcoded`) and `approval.py`
(reject/expire/get/list — pure CRUD, no hardcoded/declarative dispatch
needed, also a leaf). This `__init__.py` is what's left after pulling
those out: schema, the shared `_get_action_definition`/
`_invoke_function_side_effect`/`_apply_now` machinery, and the entry
points (`request_action`, `approve_action`,
`compensate_from_workflow_engine`) that genuinely need to know about
*both* kinds of Action and decide which to dispatch into — kept
together rather than fragmented further, the same call `ontology/
publishing.py` already made for its own widest-reaching function.
Every name every external caller (`routers/actions.py`,
`routers/objects.py`, `routers/ontology_admin.py`, `main.py`) used
before this split is re-exported here unchanged, so no call site needed
to change.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
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
    _evaluate_criteria,
    _get_unmasked_instance,
    _object_type_and_instance_id_from_instance_urn,
    _OPERATORS,
    _write_instance_edits,
    request_generic_action,
    revert_declarative_action,
)
from .hardcoded import (
    _apply_close_account,
    _apply_put_on_credit_hold,
    _compensate_close_account,
    _customer_id_from_instance_urn,
    _event,
    ACTION_DEFINITIONS,
    get_account_status,
    get_credit_holds,
    register_apply_function,
    WORKFLOW_DELEGATED_ACTIONS,
    WORKFLOW_ENGINE_URN_NAME,
    _APPLY_FUNCTIONS,
)
from .timeline import list_instance_timeline

__all__ = [
    "ensure_schema",
    "request_action",
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
    "get_credit_holds",
    "get_account_status",
    "register_apply_function",
    "ACTION_DEFINITIONS",
    "WORKFLOW_DELEGATED_ACTIONS",
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

-- Undo/revert support — additive, declarative-Action-only (the two
-- hardcoded Actions insert NULL for both, same "old and new coexist"
-- precedent `object_instance_edit`'s own docstring already established).
-- `edits` is `{property: newValue}` (already computed by
-- `_apply_declarative_edits`, just persisted here too now); `prior_values`
-- is `{property: {"existed": bool, "value": ...}}` — `existed` disambiguates
-- "this property was never set before" from "it was explicitly set to
-- JSON null", which a bare value could never do on its own.
ALTER TABLE action_invocation ADD COLUMN IF NOT EXISTS edits JSONB;
ALTER TABLE action_invocation ADD COLUMN IF NOT EXISTS prior_values JSONB;
ALTER TABLE action_invocation ADD COLUMN IF NOT EXISTS reverted_at TIMESTAMPTZ;

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

-- Carries a declarative high-risk Action's parameters across the
-- pending-approval window, so `approve_action` can apply the right
-- `edits` at decision time — the two hardcoded Actions never set this
-- (empty default), `reason` alone was always enough for them.
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

-- Generic instance-edit overlay: the reusable version of what
-- `customer_credit_hold`/`customer_account_status` above each do for
-- exactly one hardcoded Action. A *declarative* Action Type
-- (`ontology/action_types.py`) writes its `edits` here instead of
-- getting its own bespoke table — one property per row, upsert
-- (last write wins), generic to any ObjectType/instance, so a new
-- declarative Action never needs a new table or a new overlay-merge
-- function. `property_value` is JSONB so it can hold any JSON-typed
-- value (string/number/bool) generically. The two pre-existing
-- Customer Actions are untouched — they keep their own tables, proving
-- old and new coexist rather than being migrated.
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
    await conn.execute(DDL)


async def _get_action_definition(pool: asyncpg.Pool, tenant_id: str, action_name: str) -> Optional[dict]:
    """`ACTION_DEFINITIONS` first — a plain dict lookup, the two
    hardcoded Actions' behavior is completely unaffected. Falls back to
    the declarative `action_type` registry (`ontology/action_types.py`)
    for any other name, adapted into the same shape (`target_object_type`/
    `required_permission`/`risk_level`/`description`/`function_side_effect`)
    so readers that only need the adapted shape (`GET /actions`, the
    agent tool-compiler, apply paths) go through this one lookup.
    Publish-time interface checks (`_validate_implements`) resolve the
    same registries independently via `_actions_available_on_object_type`
    — they need the full set of OT-/interface-targeted actions, not a
    single-name lookup. `_declarative` carries the full registry row
    (parameters/edits/submission_criteria) for the apply path below —
    never read by anything that only needs the adapted shape.
    """
    if action_name in ACTION_DEFINITIONS:
        return ACTION_DEFINITIONS[action_name]

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
        # Public schema exposes `parameters`/`edits` so callers (including
        # the OSDK codegen generator, and the frontend's Actions-on-
        # interfaces relevance filter and inline-edit eligibility
        # inference) can discover them without a second, per-action fetch
        # of the full registry row. `submission_criteria` stays internal
        # to `_declarative` — nothing outside the apply path needs it.
        "parameters": action_type["parameters"],
        "edits": action_type["edits"],
        "_declarative": action_type,
    }


async def request_action(
    pool: asyncpg.Pool,
    *,
    action_name: str,
    tenant_id: str,
    workspace_id: str,
    customer_id: int,
    principal: Principal,
    reason: str,
    ttl_seconds: Optional[int] = None,
) -> dict:
    """The one entry point every Customer Action goes through. The caller
    never needs to know the risk level in advance.
    """
    definition = ACTION_DEFINITIONS[action_name]
    instance_urn = build_urn(tenant_id, workspace_id, "instance", f"Customer/{customer_id}")

    if definition["risk_level"] == "low":
        return await _apply_now(pool, action_name, tenant_id, workspace_id, instance_urn, customer_id, principal, reason)

    # High risk: propose only. No mutation until `approve_action`.
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
                INSERT INTO action_approval (tenant_id, action_name, instance_urn, requested_by_urn, reason, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                tenant_id, action_name, instance_urn, principal.urn, reason, expires_at,
            )
            await outbox.enqueue(conn, event)

    return {
        "status": "pending_approval",
        "approvalId": approval_id,
        "action": action_name,
        "riskLevel": definition["risk_level"],
        "expiresAt": expires_at.isoformat(),
    }


async def _invoke_function_side_effect(
    pool: asyncpg.Pool, *, tenant_id: str, workspace_id: str, action_name: str, instance_id
) -> None:
    """A Function side effect is optional operational
    enrichment, never a consistency-critical step — unlike the saga's
    Step 2 (Connectivity's external write), a failure here must never
    undo or block an Action that already committed, so it always runs
    *after* `_apply_now`'s own transaction, best-effort, logged either
    way rather than silently swallowed. Local imports (not top-level):
    this package stays decoupled from `ontology.py`/`function_registry.py`
    at module-load time, same defensive reasoning `ontology.py`'s own
    `_validate_implements`/`_validate_derived_properties` already use
    for the reverse direction.

    `instance_id` generic (not `customer_id: int`) since this now also
    runs for declarative Action Types against any ObjectType — routed
    through `_get_action_definition` (`ACTION_DEFINITIONS` first, same
    as everywhere else) rather than indexing the hardcoded dict directly.
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
    applied edits — dynamic logic (e.g. a discount derived from a
    Customer's lifetime-value model) rather than a fixed declaration.
    Unlike `_invoke_function_side_effect` this *raises* rather than
    logging-and-skipping: the edits ARE the action, it cannot proceed
    without them. Deliberately called *before* `_apply_now`/
    `approve_action` open their DB transaction — a plugin call may do
    real I/O (the real example plugin, `plugins/customer_value_model_
    function.py`, calls out to Intelligence over HTTP) and must never
    happen while holding a transaction/row lock, the same "don't hold a
    lock across a plugin's own I/O" concern `_invoke_function_side_effect`
    already avoids by running strictly after its own transaction — here
    the result is load-bearing, so it has to run first instead, but the
    transaction still never wraps it.
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
    # Parameters override translated instance fields on a name collision
    # — merged into one dict first, since unpacking two dicts with
    # overlapping keys directly into a call (`f(**a, **b)`) raises
    # `TypeError: got multiple values for keyword argument`.
    output = await plugin.call(**{**translated, **parameters})
    if not isinstance(output, dict) or not output:
        raise ValueError(
            f"{action_name}'s edit_function {function_name!r} must return a non-empty {{property: value}} "
            f"dict of edits, got {output!r}"
        )

    # Same property-control + interface-scope restrictions
    # `declarative.request_generic_action` already applies to a static
    # `edits` list — necessarily deferred here to *after* the call, since
    # a function's output can't be known before it actually runs.
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
    """`object_type`/`instance_id`/`parameters` are only ever set by
    `declarative.request_generic_action` — every existing call site
    (`request_action`, Customer-only) omits them, so
    `apply_fn = _APPLY_FUNCTIONS.get(action_name)` resolves exactly as
    `_APPLY_FUNCTIONS[action_name]` always did for the two hardcoded
    Actions, and this branch is never reached for them.
    """
    at = datetime.now(timezone.utc)
    event = _event(
        event_type="knowledge.action.invoked",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        instance_urn=instance_urn,
        actor=actor,
        payload={"action_name": action_name, "instance_urn": instance_urn, "reason": reason},
    )
    apply_fn = _APPLY_FUNCTIONS.get(action_name)
    edits_for_invocation: Optional[dict] = None
    prior_for_invocation: Optional[dict] = None

    # Function-backed resolution happens *before* the connection/
    # transaction is even acquired — see `_resolve_function_backed_edits`'s
    # docstring for why a plugin call must never happen while holding a
    # DB transaction open.
    definition: Optional[dict] = None
    resolved_function_edits: Optional[dict] = None
    if apply_fn is None:
        definition = await _get_action_definition(pool, tenant_id, action_name)
        if definition["_declarative"].get("edit_function"):
            resolved_function_edits = await _resolve_function_backed_edits(
                pool, tenant_id=tenant_id, workspace_id=workspace_id, action_name=action_name,
                definition=definition, object_type=object_type, instance_id=instance_id,
                parameters=parameters or {},
            )

    async with pool.acquire() as conn:
        async with conn.transaction():
            if apply_fn is not None:
                result = await apply_fn(conn, tenant_id, customer_id, actor, reason, at)
            else:
                action_urn = build_urn(tenant_id, "global", "action-type", action_name)
                if resolved_function_edits is not None:
                    result, prior = await _write_instance_edits(
                        conn, tenant_id, object_type, instance_id, resolved_function_edits,
                        action_urn=action_urn, actor=actor, at=at,
                    )
                else:
                    result, prior = await _apply_declarative_edits(
                        conn, tenant_id, object_type, instance_id, definition["_declarative"]["edits"],
                        parameters or {}, action_urn=action_urn, actor=actor, at=at,
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

    risk_level = definition["risk_level"] if definition is not None else (await _get_action_definition(pool, tenant_id, action_name))["risk_level"]
    return {"status": "applied", "action": action_name, "riskLevel": risk_level, "invocationId": invocation_id, **result}


async def approve_action(
    pool: asyncpg.Pool,
    *,
    approval_id: int,
    workspace_id: str,
    decider: Principal,
    note: Optional[str] = None,
) -> dict:
    """Only ever does Step 1 (the saga per this package's docstring) —
    the external step and its compensation, if the action needs either,
    are Automation's job now, triggered asynchronously by the
    `knowledge.action.invoked` event published below. `sagaStatus` in the
    response indicates `"processing"` when Automation hasn't run yet
    — poll `GET /approvals/{id}` for the eventual outcome (`approved` stays
    approved on success; Automation's compensation callback flips it to
    `failed` on failure).
    """
    preview = await pool.fetchrow("SELECT * FROM action_approval WHERE id = $1", approval_id)
    if preview is None:
        raise LookupError(f"approval {approval_id} not found")

    # Function-backed resolution happens before the row is even locked —
    # see `_resolve_function_backed_edits`'s docstring for why a plugin
    # call must never happen while holding a DB transaction/row lock. The
    # authoritative pending/expiry check still happens under the lock
    # below (unchanged); a concurrent double-approval just means this
    # pre-computed result is wastefully discarded when that recheck fails.
    definition: Optional[dict] = None
    resolved_function_edits: Optional[dict] = None
    if preview["action_name"] not in ACTION_DEFINITIONS:
        definition = await _get_action_definition(pool, preview["tenant_id"], preview["action_name"])
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

            apply_fn = _APPLY_FUNCTIONS.get(action_name)
            writeback_edits: Optional[dict] = None
            edits_for_invocation: Optional[dict] = None
            prior_for_invocation: Optional[dict] = None
            if apply_fn is not None:
                customer_id = _customer_id_from_instance_urn(instance_urn)
                result = await apply_fn(conn, tenant_id, customer_id, decider, reason, at)
            else:
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
                        parameters, action_urn=action_urn, actor=decider, at=at,
                    )
                edits_for_invocation = result
                prior_for_invocation = prior
                # Only a declarative Action Type with a writeback target
                # ever needs Automation to mirror anything to a source —
                # the two hardcoded Actions keep their existing behavior
                # (closeAccount's own saga is unaffected, untouched below).
                if definition["_declarative"].get("writeback_dataset"):
                    writeback_edits = result

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
            await outbox.enqueue(conn, event)  # Automation's Trigger fires off this event, not a direct call

    # `writeback_edits is not None` covers every declarative Action Type
    # with a writeback target — `action_name in WORKFLOW_DELEGATED_ACTIONS`
    # alone would wrongly report "completed" for one of these while
    # Automation's saga is still in flight.
    saga_status = "processing" if (action_name in WORKFLOW_DELEGATED_ACTIONS or writeback_edits is not None) else "completed"
    return {
        "status": "approved", "approvalId": approval_id, "action": action_name, "sagaStatus": saga_status,
        "invocationId": invocation_id, **result,
    }


async def compensate_from_workflow_engine(pool: asyncpg.Pool, *, approval_id: int, workspace_id: str, error: str) -> dict:
    """Entry point for `POST /internal/approvals/{id}/compensate` — the
    callback Automation's Workflow Engine makes when its own Step 2 fails.
    Looks up what `approve_action` already committed (Step 1) so the
    caller only needs to know *that* it failed, not Knowledge's own
    schema.
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
    action_name = row["action_name"]
    if action_name in ACTION_DEFINITIONS:
        # The one hardcoded case (`Customer.closeAccount`) keeps its own
        # specific compensator, untouched — same dispatch shape every
        # other apply/compensate branch in this package already uses.
        await _compensate_close_account(
            pool,
            tenant_id=row["tenant_id"],
            workspace_id=workspace_id,
            approval_id=approval_id,
            action_name=action_name,
            instance_urn=instance_urn,
            customer_id=_customer_id_from_instance_urn(instance_urn),
            decider=decider,
            error=error,
        )
    else:
        await _compensate_declarative_action(
            pool,
            tenant_id=row["tenant_id"],
            workspace_id=workspace_id,
            approval_id=approval_id,
            action_name=action_name,
            instance_urn=instance_urn,
            decider=decider,
            error=error,
        )
    return {"approvalId": approval_id, "status": "compensated"}
