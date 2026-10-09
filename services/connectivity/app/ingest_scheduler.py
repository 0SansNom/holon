"""Connectivity source scheduler loop."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import asyncpg

from holon_common import EventActor, build_urn

from . import deps, pipeline, plugin_registry, source_kinds
from .deps import SCHEDULER_ACTOR_URN, SCHEDULER_POLL_SECONDS, WORKSPACE_ID, _SCHEDULER_LOCK_KEY
from .ingest_pipeline import _run_pipeline
from .ingest_sync import _is_quiesced, _run_sync_for_dataset

logger = logging.getLogger("connectivity.scheduler")


async def run_scheduler_forever(pool: asyncpg.Pool) -> None:
    """Background scheduler loop checking for due sync sources and pipelines.

    Decouples scheduling ("when") from execution ("how") by invoking `_run_sync_for_dataset`.
    Multi-replica safe via PostgreSQL advisory locks.
    """
    async def _run_if_due(*, dataset_name: str, tenant_id: str, workspace_id: str, interval: timedelta) -> None:
        dataset_urn = build_urn(tenant_id, workspace_id, "dataset", dataset_name)
        last_finished_at = await pool.fetchval(
            "SELECT finished_at FROM sync_run WHERE tenant_id = $1 AND dataset_urn = $2 "
            "ORDER BY finished_at DESC LIMIT 1",
            tenant_id, dataset_urn,
        )
        due = last_finished_at is None or (datetime.now(timezone.utc) - last_finished_at) >= interval
        if not due:
            return
        try:
            result = await _run_sync_for_dataset(dataset_name, actor=actor, tenant_id=tenant_id, workspace_id=workspace_id)
            logger.info("scheduled sync completed for %r (tenant=%s): %d rows", dataset_name, tenant_id, result.row_count)
        except Exception:
            logger.exception("scheduled sync failed for %r (tenant=%s) — will retry next poll", dataset_name, tenant_id)

    async def _run_pipeline_if_due(*, name: str, tenant_id: str, interval: timedelta) -> None:
        # Check last successful pipeline run timestamp
        last_finished_at = await pool.fetchval(
            "SELECT finished_at FROM pipeline_run WHERE tenant_id = $1 AND pipeline_name = $2 "
            "AND status = 'succeeded' ORDER BY id DESC LIMIT 1",
            tenant_id,
            name,
        )
        due = last_finished_at is None or (datetime.now(timezone.utc) - last_finished_at) >= interval
        if not due:
            return
        try:
            await _run_pipeline(name, actor=actor, tenant_id=tenant_id)
            logger.info("scheduled pipeline run completed for %r (tenant=%s)", name, tenant_id)
        except Exception:
            logger.exception("scheduled pipeline run failed for %r (tenant=%s) — will retry next poll", name, tenant_id)

    actor = EventActor(type="service_account", urn=SCHEDULER_ACTOR_URN, on_behalf_of=None)
    while True:
        try:
            async with pool.acquire() as conn:
                got_lock = await conn.fetchval("SELECT pg_try_advisory_lock($1)", _SCHEDULER_LOCK_KEY)
                if not got_lock:
                    await asyncio.sleep(SCHEDULER_POLL_SECONDS)
                    continue
                try:
                    if await _is_quiesced(pool):
                        await asyncio.sleep(SCHEDULER_POLL_SECONDS)
                        continue
                    for kind in source_kinds.SOURCE_KINDS:
                        for source in await kind.list_all_scheduled(pool):
                            await _run_if_due(
                                dataset_name=source["name"],
                                tenant_id=source["tenant_id"],
                                workspace_id=source.get("workspace_id") or WORKSPACE_ID,
                                interval=timedelta(minutes=source["schedule_interval_minutes"]),
                            )
                    # Check scheduled connector plugins
                    plugins = await plugin_registry.list_all_scheduled_plugins(pool)
                    for plugin in plugins:
                        if not plugin["dataset_name"]:
                            continue
                        await _run_if_due(
                            dataset_name=plugin["dataset_name"],
                            tenant_id=plugin["tenant_id"],
                            workspace_id=WORKSPACE_ID,
                            interval=timedelta(minutes=plugin["schedule_interval_minutes"]),
                        )
                    pipelines_due = await pipeline.list_all_scheduled_pipelines(pool)
                    for scheduled_pipeline in pipelines_due:
                        await _run_pipeline_if_due(
                            name=scheduled_pipeline["name"],
                            tenant_id=scheduled_pipeline["tenant_id"],
                            interval=timedelta(minutes=scheduled_pipeline["schedule_interval_minutes"]),
                        )
                finally:
                    await conn.fetchval("SELECT pg_advisory_unlock($1)", _SCHEDULER_LOCK_KEY)
        except Exception:
            logger.exception("scheduler loop iteration failed — will retry")
        await asyncio.sleep(SCHEDULER_POLL_SECONDS)

