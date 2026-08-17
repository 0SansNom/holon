"""Approval lifecycle CRUD — reject, expire, get, list."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg

from holon_common import Principal, build_urn, outbox

from .hardcoded import _event

logger = logging.getLogger("knowledge.actions")

APPROVAL_TTL = timedelta(hours=24)


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
