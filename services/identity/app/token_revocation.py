"""Durable JWT revocation (jti denylist) and boot snapshot."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import asyncpg

from holon_common import EventActor, EventEnvelope, Principal, outbox
from holon_common.auth import (
    mark_jti_revoked,
    replace_disabled_principal_urns,
    replace_revoked_jtis,
)


async def enqueue_token_revoked(
    pool: asyncpg.Pool,
    *,
    jti: str,
    principal_urn: str,
    expires_at: datetime,
    actor: Principal,
    tenant_id: str,
    workspace_id: str,
) -> None:
    event_id = uuid.uuid4().hex
    event = EventEnvelope(
        event_id=event_id,
        event_type="identity.token.revoked",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        aggregate_type="Token",
        aggregate_id=jti,
        correlation_id=event_id,
        partition_key=f"{tenant_id}/{principal_urn}",
        producer="identity-platform@0.1.0",
        actor=EventActor(type=actor.type, urn=actor.urn, on_behalf_of=actor.on_behalf_of),
        payload={"jti": jti, "principal_urn": principal_urn},
    )
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO revoked_token (jti, principal_urn, expires_at)
                VALUES ($1, $2, $3)
                ON CONFLICT (jti) DO NOTHING
                """,
                jti,
                principal_urn,
                expires_at,
            )
            await outbox.enqueue(conn, event)
    mark_jti_revoked(jti)


async def is_jti_revoked_in_db(pool: asyncpg.Pool, jti: str) -> bool:
    if not jti:
        return False
    row = await pool.fetchval(
        "SELECT 1 FROM revoked_token WHERE jti = $1 AND expires_at > now()",
        jti,
    )
    return row is not None


async def load_revocation_snapshot(pool: asyncpg.Pool) -> dict[str, list[str]]:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM revoked_token WHERE expires_at <= now()")
        disabled = await conn.fetch("SELECT urn FROM principal WHERE status <> 'active'")
        jtis = await conn.fetch("SELECT jti FROM revoked_token WHERE expires_at > now()")
    return {
        "disabled_principal_urns": [r["urn"] for r in disabled],
        "revoked_jtis": [r["jti"] for r in jtis],
    }


async def hydrate_local_denylist_from_db(pool: asyncpg.Pool) -> None:
    snapshot = await load_revocation_snapshot(pool)
    replace_disabled_principal_urns(snapshot["disabled_principal_urns"])
    replace_revoked_jtis(snapshot["revoked_jtis"])
