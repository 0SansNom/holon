"""Composable FastAPI lifespan pieces shared by every Holon service.

Services keep domain seeds and workers in their own `lifespan`; this module
only covers the cross-cutting boot sequence already copy-pasted six times
(pool + migrations + durable audit + authz + outbox + principal-status).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI

from .audit import clear_durable_audit_hooks
from .audit_store import install_durable_audit, list_events_page
from .authz import PermissionClient
from .db import create_pool
from .events import EventProducer
from .migrations import run_migrations
from .observability import retry_with_backoff
from .outbox import relay_forever
from .principal_status import (
    consume_identity_auth_events,
    hydrate_revocation_snapshot,
    make_principal_status_consumer,
    refresh_revocation_snapshot_forever,
)


def reset_durable_audit(pool: Any) -> None:
    """Clear prior hooks (tests / reload) then install Postgres durable audit."""
    clear_durable_audit_hooks()
    install_durable_audit(pool)


async def boot_postgres(
    app: FastAPI,
    *,
    db_url: str,
    migrations_dir: Path,
    deps_module: Any | None = None,
) -> Any:
    """Create the pool, optionally mirror it onto ``deps.pool``, run migrations."""
    pool = await create_pool(db_url)
    app.state.pool = pool
    if deps_module is not None:
        deps_module.pool = pool
    await run_migrations(pool, migrations_dir)
    return pool


def wire_authz(
    app: FastAPI,
    *,
    spicedb_url: str,
    spicedb_preshared_key: str,
    opa_url: str,
    deps_module: Any | None = None,
) -> PermissionClient:
    """Construct PermissionClient on ``app.state`` (and optional ``deps.authz``)."""
    client = PermissionClient(spicedb_url, spicedb_preshared_key, opa_url)
    app.state.authz = client
    if deps_module is not None:
        deps_module.authz = client
    return client


@dataclass
class OutboxHandle:
    producer: EventProducer
    relay_task: asyncio.Task


async def start_outbox_relay(pool: Any, *, kafka_bootstrap: str) -> OutboxHandle:
    producer = EventProducer(kafka_bootstrap)
    await producer.start()
    relay_task = asyncio.create_task(relay_forever(pool, producer, dlq_producer=producer))
    return OutboxHandle(producer=producer, relay_task=relay_task)


async def stop_outbox(handle: OutboxHandle) -> None:
    handle.relay_task.cancel()
    await handle.producer.stop()


@dataclass
class PrincipalStatusHandle:
    consumer: Any
    status_task: asyncio.Task
    revocation_refresh_task: asyncio.Task


async def start_principal_status(
    *,
    kafka_bootstrap: str,
    service_name: str,
    authz: PermissionClient,
    dlq_producer: Any | None = None,
    hydrate: bool = True,
) -> PrincipalStatusHandle:
    consumer = make_principal_status_consumer(
        kafka_bootstrap, service_name=service_name, dlq_producer=dlq_producer
    )
    status_task = asyncio.create_task(consume_identity_auth_events(consumer, authz=authz))
    if hydrate:
        await retry_with_backoff(hydrate_revocation_snapshot, what="identity revocation snapshot")
    revocation_refresh_task = asyncio.create_task(refresh_revocation_snapshot_forever())
    return PrincipalStatusHandle(
        consumer=consumer,
        status_task=status_task,
        revocation_refresh_task=revocation_refresh_task,
    )


async def stop_principal_status(handle: PrincipalStatusHandle) -> None:
    handle.revocation_refresh_task.cancel()
    handle.status_task.cancel()
    await handle.consumer.stop()


async def list_audit_events_http(
    pool: Any,
    tenant_id: str,
    *,
    category: Optional[str] = None,
    action: Optional[str] = None,
    actor: Optional[str] = None,
    outcome: Optional[str] = None,
    traceId: Optional[str] = None,
    pageSize: Optional[int] = None,
    pageToken: Optional[str] = None,
) -> dict:
    """Wire-shaped ``GET …/audit-events`` body after the caller has authorized."""
    return await list_events_page(
        pool,
        tenant_id,
        category=category,
        action=action,
        actor_urn=actor,
        outcome=outcome,
        trace_id=traceId,
        page_size=50 if pageSize is None else pageSize,
        page_token=pageToken,
    )
