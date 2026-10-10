"""Dataset sync execution for Connectivity (Iceberg write + finalize)."""
from __future__ import annotations

import asyncio
import functools
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

import asyncpg
import httpx

from holon_common import EventActor, EventEnvelope, HolonError, build_urn, outbox
from holon_common.audit import emit_audit
from holon_common.connector_safety import ConnectorSafetyError
from holon_common.correlation import current_correlation_id

from . import deps, iceberg_writer, plugin_registry, source_kinds, source_registry_base
from .deps import ICEBERG_CONFIG, TENANT_ID, WORKSPACE_ID, SyncResult

logger = logging.getLogger("connectivity.scheduler")


async def _is_quiesced(pool: asyncpg.Pool) -> bool:
    value = await pool.fetchval("SELECT value FROM connectivity_runtime WHERE key = 'quiesced'")
    return value == "true"

async def _finalize_sync(
    *,
    connector_urn: str,
    dataset_name: str,
    result: iceberg_writer.IcebergWriteResult,
    started_at: datetime,
    finished_at: datetime,
    actor: EventActor,
    source_dataset_version_urn: Optional[str] = None,
    producer: Optional[dict] = None,
    tenant_id: str = TENANT_ID,
    workspace_id: str = WORKSPACE_ID,
) -> SyncResult:
    """Record sync_run entry, enqueue completion event to outbox, and audit."""
    dataset_urn = build_urn(tenant_id, workspace_id, "dataset", dataset_name)
    dataset_version_urn = build_urn(tenant_id, workspace_id, "dataset-version", str(result.snapshot_id))
    event_id = uuid.uuid4().hex

    event = EventEnvelope(
        event_id=event_id,
        event_type="connectivity.sync.completed",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        aggregate_type="Connector",
        aggregate_id=connector_urn,
        correlation_id=current_correlation_id() or event_id,
        partition_key=f"{tenant_id}/{dataset_urn}",
        producer="connectivity-platform@0.1.0",
        actor=actor,
        payload={
            "connector_urn": connector_urn,
            "dataset_name": dataset_name,
            "dataset_urn": dataset_urn,
            "dataset_version_urn": dataset_version_urn,
            "iceberg_namespace": result.namespace,
            "iceberg_table": result.table,
            "snapshot_id": result.snapshot_id,
            "row_count": result.row_count,
            "location": result.location,
            "source_dataset_version_urn": source_dataset_version_urn,
            "producer": producer,
        },
    )

    async with deps.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO sync_run (
                    tenant_id, connector_urn, dataset_urn, dataset_version_urn,
                    iceberg_namespace, iceberg_table, snapshot_id, row_count,
                    started_at, finished_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                tenant_id,
                connector_urn,
                dataset_urn,
                dataset_version_urn,
                result.namespace,
                result.table,
                result.snapshot_id,
                result.row_count,
                started_at,
                finished_at,
            )
            await outbox.enqueue(conn, event)

    emit_audit(
        category="access",
        action="connectivity.sync.completed",
        outcome="success",
        tenant_id=tenant_id,
        actor_urn=actor.urn,
        actor_type=actor.type,
        resource_type="dataset",
        resource_urn=dataset_urn,
        extra={
            "connector_urn": connector_urn,
            "dataset_version_urn": dataset_version_urn,
            "snapshot_id": result.snapshot_id,
            "row_count": result.row_count,
        },
    )

    return SyncResult(
        dataset_urn=dataset_urn,
        dataset_version_urn=dataset_version_urn,
        snapshot_id=result.snapshot_id,
        row_count=result.row_count,
        location=result.location,
    )

async def _run_sync_for_dataset(
    dataset_name: str, *, actor: EventActor, tenant_id: str = TENANT_ID, workspace_id: str = WORKSPACE_ID
) -> SyncResult:
    """Execute sync pipeline: fetch data from source, write Iceberg snapshot, and finalize."""
    write_mode: Literal["overwrite", "append"] = "overwrite"
    plugin = await plugin_registry.load_active_plugin_for_dataset(deps.pool, dataset_name, tenant_id)
    if plugin is not None:
        local_name = plugin.manifest.connector_local_name or f"plugin-{plugin.manifest.name}"
        connector_urn = build_urn(tenant_id, "global", "connector", local_name)

        async def read():
            # ConnectorPlugin.fetch() returns plain rows (no cursor to
            # defer) — wrap so the call site below can treat every
            # source uniformly as (rows, commit_cursor).
            plugin_rows = await plugin.fetch()
            return plugin_rows, None
    else:
        resolved = await source_kinds.resolve_registered_source(deps.pool, tenant_id, dataset_name)
        if resolved is None:
            raise HolonError.not_found(
                "DatasetNotFound",
                f"unknown dataset: {dataset_name}",
                dataset_name=dataset_name,
            )
        kind, source = resolved
        if source["status"] != "active":
            raise HolonError.conflict(
                "SourceDisabled",
                f"source {dataset_name!r} is disabled — enable it first",
                dataset_name=dataset_name,
            )
        connector_urn = build_urn(tenant_id, "global", "connector", kind.connector_local_name(dataset_name))
        read = functools.partial(kind.fetch_for_dataset, deps.pool, tenant_id, dataset_name)
        if kind.uses_append(source):
            write_mode = "append"

    started_at = datetime.now(timezone.utc)
    commit_cursor = None
    try:
        rows, commit_cursor = await read()
    except source_registry_base.SourceFetchError as exc:
        raise HolonError.invalid_argument('DatasetValidationFailed', str(exc)) from exc
    except ConnectorSafetyError as exc:
        raise HolonError.invalid_argument('DatasetValidationFailed', str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HolonError.invalid_argument('SourceHttpError', f"source returned {exc.response.status_code}: {exc.response.text[:300]}") from exc
    except httpx.RequestError as exc:
        raise HolonError.invalid_argument('SourceUnreachable', f"could not reach the source: {exc}") from exc
    result = await asyncio.to_thread(
        iceberg_writer.write_snapshot, rows, dataset_name, mode=write_mode, tenant_id=tenant_id, **ICEBERG_CONFIG
    )
    if commit_cursor is not None:
        await commit_cursor()
    finished_at = datetime.now(timezone.utc)

    return await _finalize_sync(
        connector_urn=connector_urn,
        dataset_name=dataset_name,
        result=result,
        started_at=started_at,
        finished_at=finished_at,
        actor=actor,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )

