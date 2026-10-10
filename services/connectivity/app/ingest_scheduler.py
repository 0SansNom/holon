"""Connectivity source scheduler loop."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import asyncpg

from holon_common import EventActor, build_urn

from . import pipeline, plugin_registry, source_kinds
from .deps import (
    SCHEDULER_ACTOR_URN,
    SCHEDULER_FETCH_TIMEOUT_SECONDS,
    SCHEDULER_MAX_CONCURRENCY,
    SCHEDULER_POLL_SECONDS,
    SCHEDULER_TENANT_CONCURRENCY,
    WORKSPACE_ID,
    _SCHEDULER_LOCK_KEY,
)
from .ingest_pipeline import _run_pipeline
from .ingest_sync import _is_quiesced, _run_sync_for_dataset
from .scheduler_jobs import ScheduledJobRunner

logger = logging.getLogger("connectivity.scheduler")


async def _is_due(pool: asyncpg.Pool, sql: str, tenant_id: str, key: str, interval: timedelta) -> bool:
    last_finished_at = await pool.fetchval(sql, tenant_id, key)
    return last_finished_at is None or (datetime.now(timezone.utc) - last_finished_at) >= interval


_LAST_SYNC_SQL = (
    "SELECT finished_at FROM sync_run WHERE tenant_id = $1 AND dataset_urn = $2 "
    "ORDER BY finished_at DESC LIMIT 1"
)
_LAST_PIPELINE_SQL = (
    "SELECT finished_at FROM pipeline_run WHERE tenant_id = $1 AND pipeline_name = $2 "
    "AND status = 'succeeded' ORDER BY id DESC LIMIT 1"
)


async def _submit_due_jobs(pool: asyncpg.Pool, runner: ScheduledJobRunner, actor: EventActor) -> None:
    async def submit_sync(*, dataset_name: str, tenant_id: str, workspace_id: str, interval_minutes: int) -> None:
        key = ("sync", tenant_id, dataset_name)
        if runner.is_running(key):
            return
        dataset_urn = build_urn(tenant_id, workspace_id, "dataset", dataset_name)
        if not await _is_due(pool, _LAST_SYNC_SQL, tenant_id, dataset_urn, timedelta(minutes=interval_minutes)):
            return

        async def job() -> None:
            result = await _run_sync_for_dataset(
                dataset_name,
                actor=actor,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                fetch_timeout=SCHEDULER_FETCH_TIMEOUT_SECONDS,
            )
            logger.info("scheduled sync completed for %r (tenant=%s): %d rows", dataset_name, tenant_id, result.row_count)

        runner.submit(key, tenant_id, job)

    for kind in source_kinds.SOURCE_KINDS:
        for source in await kind.list_all_scheduled(pool):
            await submit_sync(
                dataset_name=source["name"],
                tenant_id=source["tenant_id"],
                workspace_id=source.get("workspace_id") or WORKSPACE_ID,
                interval_minutes=source["schedule_interval_minutes"],
            )
    for plugin in await plugin_registry.list_all_scheduled_plugins(pool):
        if not plugin["dataset_name"]:
            continue
        await submit_sync(
            dataset_name=plugin["dataset_name"],
            tenant_id=plugin["tenant_id"],
            workspace_id=WORKSPACE_ID,
            interval_minutes=plugin["schedule_interval_minutes"],
        )
    for scheduled_pipeline in await pipeline.list_all_scheduled_pipelines(pool):
        name = scheduled_pipeline["name"]
        tenant_id = scheduled_pipeline["tenant_id"]
        key = ("pipeline", tenant_id, name)
        if runner.is_running(key):
            continue
        interval = timedelta(minutes=scheduled_pipeline["schedule_interval_minutes"])
        if not await _is_due(pool, _LAST_PIPELINE_SQL, tenant_id, name, interval):
            continue

        async def job(name: str = name, tenant_id: str = tenant_id) -> None:
            await _run_pipeline(name, actor=actor, tenant_id=tenant_id)
            logger.info("scheduled pipeline run completed for %r (tenant=%s)", name, tenant_id)

        runner.submit(key, tenant_id, job)


async def run_scheduler_forever(pool: asyncpg.Pool) -> None:
    """Background scheduler: submits due sources, plugins and pipelines to a bounded runner.

    One replica leads by holding a PostgreSQL advisory lock on a dedicated connection.
    Jobs run in the background, capped globally and per tenant, so a slow source no
    longer delays other tenants' schedules. Losing the lock cancels the leader's jobs
    so a new leader never runs them twice.
    """
    actor = EventActor(type="service_account", urn=SCHEDULER_ACTOR_URN, on_behalf_of=None)
    runner = ScheduledJobRunner(
        max_concurrency=SCHEDULER_MAX_CONCURRENCY,
        tenant_concurrency=SCHEDULER_TENANT_CONCURRENCY,
    )
    while True:
        try:
            async with pool.acquire() as conn:
                if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", _SCHEDULER_LOCK_KEY):
                    await asyncio.sleep(SCHEDULER_POLL_SECONDS)
                    continue
                try:
                    while True:
                        runner.reap()
                        try:
                            if not await _is_quiesced(pool):
                                await _submit_due_jobs(pool, runner, actor)
                        except Exception:
                            logger.exception("scheduler poll failed — will retry")
                        await asyncio.sleep(SCHEDULER_POLL_SECONDS)
                        await conn.fetchval("SELECT 1")
                finally:
                    await runner.cancel_all()
                    try:
                        await conn.fetchval("SELECT pg_advisory_unlock($1)", _SCHEDULER_LOCK_KEY)
                    except Exception:
                        logger.warning("scheduler could not release its advisory lock", exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduler lost its leadership connection — will retry")
        await asyncio.sleep(SCHEDULER_POLL_SECONDS)
