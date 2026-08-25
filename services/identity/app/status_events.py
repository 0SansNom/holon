"""Outbox enqueue for `identity.principal.status_changed`."""

from __future__ import annotations

import uuid

import asyncpg

from holon_common import EventActor, EventEnvelope, Principal, outbox
from holon_common.auth import mark_principal_disabled, mark_principal_enabled


async def enqueue_principal_status_event(
    pool: asyncpg.Pool,
    *,
    target_principal_urn: str,
    status: str,
    actor: Principal,
    tenant_id: str,
    workspace_id: str,
) -> dict | None:
    event_id = uuid.uuid4().hex
    event = EventEnvelope(
        event_id=event_id,
        event_type="identity.principal.status_changed",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        aggregate_type="Principal",
        aggregate_id=target_principal_urn,
        correlation_id=event_id,
        partition_key=f"{tenant_id}/{target_principal_urn}",
        producer="identity-platform@0.1.0",
        actor=EventActor(type=actor.type, urn=actor.urn, on_behalf_of=actor.on_behalf_of),
        payload={"principal_urn": target_principal_urn, "status": status},
    )
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("UPDATE principal SET status = $2 WHERE urn = $1", target_principal_urn, status)
            await outbox.enqueue(conn, event)
    if status == "active":
        mark_principal_enabled(target_principal_urn)
    else:
        mark_principal_disabled(target_principal_urn)
    row = await pool.fetchrow("SELECT * FROM principal WHERE urn = $1", target_principal_urn)
    return dict(row) if row else None
