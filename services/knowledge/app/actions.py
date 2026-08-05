"""`Action` — a named, typed, permissioned and audited
mutation of a business object. Humans, applications, workflows and agents
all go through this same point of passage.

Two Actions exist, chosen to make `risk_level` actually branch
behavior:

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
a multi-context saga across Knowledge + Connectivity.

**Orchestration ownership**: sagas are implemented by the Workflow Engine (the
Automation platform, `services/automation/`), not by whichever service happens to own Step 1.
So this module only ever does **Step 1** — the local mutation, committed
on its own — and publishes `knowledge.action.invoked`. Automation's
`workflow.py` is what's actually listening for that event, calling
Connectivity's write endpoint (Step 2), and — if that fails — calling
back here via `POST /internal/approvals/{id}/compensate` to run
`_compensate_close_account` below, a second, explicit local transaction
that undoes Step 1.
`putOnCreditHold` has no external step, so it's never even in
`WORKFLOW_DELEGATED_ACTIONS`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg

from holon_common import EventActor, EventEnvelope, Principal, build_urn, outbox

logger = logging.getLogger("knowledge.actions")

# Default human-in-the-loop review window (a pending high-risk request that
# never expires is indistinguishable from one still being reviewed). `request_action`'s
# caller-supplied `ttl_seconds` override exists purely as a test/demo hook.
APPROVAL_TTL = timedelta(hours=24)

# `description`: a mandatory natural-language description of what invoking this Action does.
# Exposed via `GET /actions`/`GET /actions/{name}` below — an agent tool-compiler
# reads this field to generate a tool's description.
ACTION_DEFINITIONS = {
    "Customer.putOnCreditHold": {
        "target_object_type": "Customer",
        "required_permission": "write",
        "risk_level": "low",
        "description": "Places a Customer's account on credit hold, recording a reason. "
        "Applies immediately (low risk — reversible, no external write, no deletion).",
    },
    "Customer.closeAccount": {
        "target_object_type": "Customer",
        "required_permission": "write",
        "risk_level": "high",
        "description": "Closes a Customer's account. Proposes a human-in-the-loop approval "
        "request (high risk — deletion-class, writes back to source system).",
    },
}

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
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


def _customer_id_from_instance_urn(instance_urn: str) -> int:
    return int(instance_urn.rsplit("/", 1)[-1])


_APPLY_FUNCTIONS = {}


def register_apply_function(action_name: str):
    def _decorator(func):
        _APPLY_FUNCTIONS[action_name] = func
        return func

    return _decorator


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


async def _apply_now(
    pool: asyncpg.Pool,
    action_name: str,
    tenant_id: str,
    workspace_id: str,
    instance_urn: str,
    customer_id: int,
    actor: Principal,
    reason: str,
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
    apply_fn = _APPLY_FUNCTIONS[action_name]

    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await apply_fn(conn, tenant_id, customer_id, actor, reason, at)
            await conn.execute(
                "INSERT INTO action_invocation (tenant_id, action_name, instance_urn, actor_urn, actor_type, reason) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                tenant_id, action_name, instance_urn, actor.urn, actor.type, reason,
            )
            await outbox.enqueue(conn, event)

    return {"status": "applied", "action": action_name, "riskLevel": ACTION_DEFINITIONS[action_name]["risk_level"], **result}


WORKFLOW_DELEGATED_ACTIONS = {"Customer.closeAccount"}

WORKFLOW_ENGINE_URN_NAME = "automation-workflow-engine"


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


async def approve_action(
    pool: asyncpg.Pool,
    *,
    approval_id: int,
    workspace_id: str,
    decider: Principal,
    note: Optional[str] = None,
) -> dict:
    """Only ever does Step 1 (the saga per this module's docstring) —
    the external step and its compensation, if the action needs either,
    are Automation's job now, triggered asynchronously by the
    `knowledge.action.invoked` event published below. `sagaStatus` in the
    response reflects that honestly: `"processing"` means Automation
    hasn't run yet, not that anything failed — poll `GET /approvals/{id}`
    for the eventual outcome (`approved` stays approved on success;
    Automation's compensation callback flips it to `failed` on failure).
    """
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
            customer_id = _customer_id_from_instance_urn(instance_urn)
            at = datetime.now(timezone.utc)

            apply_fn = _APPLY_FUNCTIONS[action_name]
            result = await apply_fn(conn, tenant_id, customer_id, decider, reason, at)

            await conn.execute(
                "UPDATE action_approval SET status = 'approved', decided_by_urn = $1, decided_at = $2, decision_note = $3 WHERE id = $4",
                decider.urn, at, note, approval_id,
            )
            await conn.execute(
                "INSERT INTO action_invocation (tenant_id, action_name, instance_urn, actor_urn, actor_type, reason) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                tenant_id, action_name, instance_urn, decider.urn, decider.type, reason,
            )
            event = _event(
                event_type="knowledge.action.invoked",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                instance_urn=instance_urn,
                actor=decider,
                payload={"action_name": action_name, "instance_urn": instance_urn, "reason": reason, "approval_id": approval_id},
            )
            await outbox.enqueue(conn, event)  # Automation's Trigger fires off this event, not a direct call

    saga_status = "processing" if action_name in WORKFLOW_DELEGATED_ACTIONS else "completed"
    return {"status": "approved", "approvalId": approval_id, "action": action_name, "sagaStatus": saga_status, **result}


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
    await _compensate_close_account(
        pool,
        tenant_id=row["tenant_id"],
        workspace_id=workspace_id,
        approval_id=approval_id,
        action_name=row["action_name"],
        instance_urn=instance_urn,
        customer_id=_customer_id_from_instance_urn(instance_urn),
        decider=decider,
        error=error,
    )
    return {"approvalId": approval_id, "status": "compensated"}


async def reject_action(pool: asyncpg.Pool, *, approval_id: int, workspace_id: str, decider: Principal, note: Optional[str] = None) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM action_approval WHERE id = $1 FOR UPDATE", approval_id)
            if row is None:
                raise LookupError(f"approval {approval_id} not found")
            if row["status"] != "pending":
                raise ValueError(f"approval {approval_id} is already {row['status']}")
            if row["expires_at"] < datetime.now(timezone.utc):
                raise ValueError(f"approval {approval_id} has expired")

            at = datetime.now(timezone.utc)
            await conn.execute(
                "UPDATE action_approval SET status = 'rejected', decided_by_urn = $1, decided_at = $2, decision_note = $3 WHERE id = $4",
                decider.urn, at, note, approval_id,
            )
            event = _event(
                event_type="knowledge.action.rejected",
                tenant_id=row["tenant_id"],
                workspace_id=workspace_id,
                instance_urn=row["instance_urn"],
                actor=decider,
                payload={"action_name": row["action_name"], "instance_urn": row["instance_urn"], "note": note},
            )
            await outbox.enqueue(conn, event)

    return {"status": "rejected", "approvalId": approval_id}


async def sweep_expired_approvals(pool: asyncpg.Pool, workspace_id: str) -> int:
    """Transitions overdue `pending` approvals to the terminal `expired`
    state and publishes `knowledge.action.approval_expired` for each — the
    real "notification": an actual downstream consumer (Slack bot, email,
    whatever) is out of scope, same as every other cross-cutting concern
    this build leaves to a future subscriber of the Platform Event Bus.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                UPDATE action_approval SET status = 'expired'
                WHERE status = 'pending' AND expires_at < now()
                RETURNING id, tenant_id, action_name, instance_urn
                """
            )
            for row in rows:
                event = _event(
                    event_type="knowledge.action.approval_expired",
                    tenant_id=row["tenant_id"],
                    workspace_id=workspace_id,
                    instance_urn=row["instance_urn"],
                    actor=Principal(
                        urn=build_urn(row["tenant_id"], "global", "service-account", "knowledge-expiry-sweep"),
                        type="service_account",
                        tenant_id=row["tenant_id"],
                        display_name="Knowledge Expiry Sweep",
                    ),
                    payload={
                        "action_name": row["action_name"],
                        "instance_urn": row["instance_urn"],
                        "approval_id": row["id"],
                    },
                )
                await outbox.enqueue(conn, event)
    return len(rows)


async def sweep_expired_approvals_forever(pool: asyncpg.Pool, workspace_id: str, poll_interval: float = 5.0) -> None:
    """Same shape as `holon_common.outbox.relay_forever` — a lone background
    task per process is enough while only one Knowledge replica writes here.
    """
    while True:
        try:
            expired_count = await sweep_expired_approvals(pool, workspace_id)
            if expired_count:
                logger.info("expired %d overdue approval(s)", expired_count)
        except Exception:
            logger.exception("approval expiry sweep error, retrying in %ss", poll_interval)
        await asyncio.sleep(poll_interval)


async def get_approval(pool: asyncpg.Pool, approval_id: int) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM action_approval WHERE id = $1", approval_id)
    return dict(row) if row else None


async def list_approvals(pool: asyncpg.Pool, tenant_id: str, status: Optional[str] = None) -> list[dict]:
    if status:
        rows = await pool.fetch(
            "SELECT * FROM action_approval WHERE tenant_id = $1 AND status = $2 ORDER BY requested_at DESC",
            tenant_id, status,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM action_approval WHERE tenant_id = $1 ORDER BY requested_at DESC", tenant_id
        )
    return [dict(row) for row in rows]


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
