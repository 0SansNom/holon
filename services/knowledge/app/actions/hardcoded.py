"""The two hardcoded Customer Actions — `putOnCreditHold` (low risk,
applies immediately) and `closeAccount` (high risk, deletion-class,
writes back to `source_erp.customers` via the saga Automation's
Workflow Engine owns Step 2 of). A leaf module: no import from
`__init__.py` or `declarative.py` — `__init__.py`'s shared orchestration
(`_apply_now`/`approve_action`) imports `_APPLY_FUNCTIONS`/
`ACTION_DEFINITIONS` from here, dispatching into them by name, exactly
the same way before this package existed. `_event` is defined here (not
in `__init__.py`) specifically so `declarative.py` can import it too
without creating a package-internal import cycle — a leaf importing a
sibling leaf is fine, a leaf importing back from `__init__.py` is not.

See the package's `__init__.py` module docstring for the two Actions'
full risk-level/saga story — kept there since it explains behavior that
spans this file and `declarative.py` both, not just this one.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import asyncpg

from holon_common import EventActor, EventEnvelope, Principal, outbox

# `description`: a mandatory natural-language description of what invoking this Action does.
# Exposed via `GET /actions`/`GET /actions/{name}` — an agent tool-compiler
# reads this field to generate a tool's description.
ACTION_DEFINITIONS = {
    "Customer.putOnCreditHold": {
        "target_object_type": "Customer",
        "required_permission": "write",
        "risk_level": "low",
        "description": "Places a Customer's account on credit hold, recording a reason. "
        "Applies immediately (low risk — reversible, no external write, no deletion).",
        # an Action can name a
        # registered Function plugin as a best-effort side effect, run
        # after the primary mutation commits — see `_invoke_function_side_effect`.
        "function_side_effect": "lifetime_tier",
    },
    "Customer.closeAccount": {
        "target_object_type": "Customer",
        "required_permission": "write",
        "risk_level": "high",
        "description": "Closes a Customer's account. Proposes a human-in-the-loop approval "
        "request (high risk — deletion-class, writes back to source system).",
    },
}

WORKFLOW_DELEGATED_ACTIONS = {"Customer.closeAccount"}

WORKFLOW_ENGINE_URN_NAME = "automation-workflow-engine"

_APPLY_FUNCTIONS = {}


def register_apply_function(action_name: str):
    def _decorator(func):
        _APPLY_FUNCTIONS[action_name] = func
        return func

    return _decorator


def _customer_id_from_instance_urn(instance_urn: str) -> int:
    return int(instance_urn.rsplit("/", 1)[-1])


def _event(*, event_type: str, tenant_id: str, workspace_id: str, instance_urn: str, actor: Principal, payload: dict) -> EventEnvelope:
    event_id = uuid.uuid4().hex
    return EventEnvelope(
        event_id=event_id,
        event_type=event_type,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        aggregate_type="ObjectType",
        aggregate_id=instance_urn,
        correlation_id=event_id,
        partition_key=f"{tenant_id}/{instance_urn}",
        producer="knowledge-platform@0.1.0",
        actor=EventActor(type=actor.type, urn=actor.urn, on_behalf_of=actor.on_behalf_of),
        payload=payload,
    )


@register_apply_function("Customer.putOnCreditHold")
async def _apply_put_on_credit_hold(
    conn: asyncpg.Connection, tenant_id: str, customer_id: int, actor: Principal, reason: str, at: datetime
) -> dict:
    await conn.execute(
        """
        INSERT INTO customer_credit_hold (customer_id, tenant_id, on_hold, reason, set_by_urn, set_at)
        VALUES ($1, $2, true, $3, $4, $5)
        ON CONFLICT (customer_id) DO UPDATE SET
            on_hold = EXCLUDED.on_hold, reason = EXCLUDED.reason,
            set_by_urn = EXCLUDED.set_by_urn, set_at = EXCLUDED.set_at
        """,
        customer_id, tenant_id, reason, actor.urn, at,
    )
    return {"customerId": customer_id, "onHold": True, "reason": reason, "setBy": actor.urn, "setAt": at.isoformat()}


@register_apply_function("Customer.closeAccount")
async def _apply_close_account(
    conn: asyncpg.Connection, tenant_id: str, customer_id: int, actor: Principal, reason: str, at: datetime
) -> dict:
    await conn.execute(
        """
        INSERT INTO customer_account_status (customer_id, tenant_id, closed, reason, set_by_urn, set_at)
        VALUES ($1, $2, true, $3, $4, $5)
        ON CONFLICT (customer_id) DO UPDATE SET
            closed = EXCLUDED.closed, reason = EXCLUDED.reason,
            set_by_urn = EXCLUDED.set_by_urn, set_at = EXCLUDED.set_at
        """,
        customer_id, tenant_id, reason, actor.urn, at,
    )
    return {"customerId": customer_id, "accountClosed": True, "reason": reason, "setBy": actor.urn, "setAt": at.isoformat()}


async def _compensate_close_account(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    workspace_id: str,
    approval_id: int,
    action_name: str,
    instance_urn: str,
    customer_id: int,
    decider: Principal,
    error: str,
) -> None:
    """Compensation step — a second, explicit local transaction, not
    a rollback of Step 1's already-committed one. Reverts
    the `customer_account_status` overlay and marks the approval `failed`,
    a terminal state distinct from `pending`/`approved`/`rejected` so it's
    never mistaken for a successful close. Called from
    `POST /internal/approvals/{id}/compensate`, invoked by Automation's
    Workflow Engine when its own Step 2 fails — Automation can't touch
    this overlay directly (separate service, separate database), so
    reverting it is still Knowledge's own concern.
    """
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
                """
                UPDATE customer_account_status SET closed = false, reason = $1, set_by_urn = $2, set_at = $3
                WHERE customer_id = $4 AND tenant_id = $5
                """,
                f"compensated: {error}", decider.urn, at, customer_id, tenant_id,
            )
            await conn.execute(
                "UPDATE action_approval SET status = 'failed', decision_note = $1 WHERE id = $2",
                f"compensated: {error}", approval_id,
            )
            await outbox.enqueue(conn, event)


async def get_credit_holds(pool: asyncpg.Pool, customer_ids: list[int]) -> dict[int, dict]:
    if not customer_ids:
        return {}
    rows = await pool.fetch(
        "SELECT customer_id, on_hold, reason, set_by_urn, set_at FROM customer_credit_hold WHERE customer_id = ANY($1::int[])",
        customer_ids,
    )
    return {row["customer_id"]: dict(row) for row in rows}


async def get_account_status(pool: asyncpg.Pool, customer_ids: list[int]) -> dict[int, dict]:
    if not customer_ids:
        return {}
    rows = await pool.fetch(
        "SELECT customer_id, closed, reason, set_by_urn, set_at FROM customer_account_status WHERE customer_id = ANY($1::int[])",
        customer_ids,
    )
    return {row["customer_id"]: dict(row) for row in rows}
