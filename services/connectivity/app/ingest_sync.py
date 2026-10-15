"""Dataset sync execution for Connectivity (Iceberg write + finalize)."""
from __future__ import annotations

import asyncio
import functools
import logging
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator, Literal, Optional

import asyncpg
import httpx

from holon_common import EventActor, EventEnvelope, HolonError, build_urn, outbox
from holon_common.audit import emit_audit
from holon_common.connector_safety import ConnectorSafetyError
from holon_common.correlation import current_correlation_id

from . import deps, iceberg_writer, plugin_registry, source_kinds, source_registry_base
from .cursor_window import resume_is_newer
from .deps import ICEBERG_CONFIG, INGEST_BATCH_ROWS, TENANT_ID, WORKSPACE_ID, SyncResult

logger = logging.getLogger("connectivity.scheduler")

_SYNC_ERROR_MAX_CHARS = 2000

# kind name → (table, cursor column, boundary column or None)
_RESUME_TABLES = {
    "generic_rest": ("generic_rest_source", "last_cursor_value", "cursor_boundary_keys"),
    "sql": ("sql_source", "last_cursor_value", "cursor_boundary_keys"),
    "salesforce": ("salesforce_source", "last_cursor_value", "cursor_boundary_keys"),
    "object": ("object_source", "last_synced_key", None),
    "sftp": ("sftp_source", "last_synced_path", None),
}


async def _adopt_committed_resume(pool: asyncpg.Pool, *, tenant_id: str, dataset_name: str, kind_name: str) -> None:
    """If the last snapshot's cursor never reached the database, store it before reading again."""
    spec = _RESUME_TABLES.get(kind_name)
    if spec is None:
        return
    table, column, boundary_column = spec
    cursor, boundary = await asyncio.to_thread(
        iceberg_writer.read_snapshot_resume, dataset_name, tenant_id=tenant_id, **ICEBERG_CONFIG
    )
    if not cursor:
        return
    if boundary_column:
        current = await pool.fetchrow(
            f"SELECT {column}, {boundary_column} FROM {table} WHERE tenant_id = $1 AND name = $2",
            tenant_id,
            dataset_name,
        )
        if current is None:
            return
        stored = current[column]
        if stored == cursor and (current[boundary_column] or "") == (boundary or ""):
            return
        if stored is not None and stored != cursor and not resume_is_newer(cursor, stored):
            return
        await source_registry_base.make_property_cursor_commit(
            pool,
            table=table,
            tenant_id=tenant_id,
            name=dataset_name,
            cursor=cursor,
            boundary_keys=boundary or "[]",
        )()
        return
    stored = await pool.fetchval(
        f"SELECT {column} FROM {table} WHERE tenant_id = $1 AND name = $2",
        tenant_id,
        dataset_name,
    )
    if not resume_is_newer(cursor, stored):
        return
    await source_registry_base.make_column_cursor_commit(
        pool,
        table=table,
        tenant_id=tenant_id,
        name=dataset_name,
        column=column,
        value=cursor,
    )()


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

async def _row_batches(source: list[dict] | AsyncIterator[list[dict]], size: int) -> AsyncIterator[list[dict]]:
    """Regroup a source's rows (a list, or batches of any size) into batches of `size`."""
    if isinstance(source, list):
        for start in range(0, len(source), size):
            yield source[start:start + size]
        return
    pending: list[dict] = []
    async for rows in source:
        pending.extend(rows)
        while len(pending) >= size:
            yield pending[:size]
            pending = pending[size:]
    if pending:
        yield pending


async def _record_sync_failure(
    *,
    tenant_id: str,
    workspace_id: str,
    connector_urn: str,
    dataset_name: str,
    actor: EventActor,
    started_at: datetime,
    exc: Exception,
) -> None:
    """Keep a failed or timed-out sync visible as a run, next to the successes in `sync_run`."""
    dataset_urn = build_urn(tenant_id, workspace_id, "dataset", dataset_name)
    error_name = exc.error_name if isinstance(exc, HolonError) else type(exc).__name__
    timed_out = error_name == "SourceFetchTimeout"
    try:
        await deps.pool.execute(
            """
            INSERT INTO sync_failure (
                tenant_id, connector_urn, dataset_urn, error_name, error, timed_out, started_at, finished_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            tenant_id,
            connector_urn,
            dataset_urn,
            error_name,
            str(exc)[:_SYNC_ERROR_MAX_CHARS],
            timed_out,
            started_at,
            datetime.now(timezone.utc),
        )
    except Exception:
        logger.exception("could not record the failed sync of %r (tenant=%s)", dataset_name, tenant_id)
    emit_audit(
        category="access",
        action="connectivity.sync.failed",
        outcome="failure",
        tenant_id=tenant_id,
        actor_urn=actor.urn,
        actor_type=actor.type,
        resource_type="dataset",
        resource_urn=dataset_urn,
        extra={"connector_urn": connector_urn, "error_name": error_name, "timed_out": timed_out},
    )

async def _run_sync_for_dataset(
    dataset_name: str,
    *,
    actor: EventActor,
    tenant_id: str = TENANT_ID,
    workspace_id: str = WORKSPACE_ID,
    fetch_timeout: Optional[float] = None,
) -> SyncResult:
    """Execute sync pipeline: fetch data from source, write Iceberg snapshot, and finalize."""
    write_mode: Literal["overwrite", "append"] = "overwrite"
    kind_name: Optional[str] = None
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
        kind_name = getattr(kind, "name", None)
        read = functools.partial(kind.fetch_for_dataset, deps.pool, tenant_id, dataset_name)
        if kind.uses_append(source):
            write_mode = "append"

    started_at = datetime.now(timezone.utc)
    try:
        commit_cursor = None
        writer = iceberg_writer.SnapshotWriter(dataset_name, mode=write_mode, tenant_id=tenant_id, **ICEBERG_CONFIG)
        fetch_deadline = asyncio.timeout(fetch_timeout)
        try:
            # The deadline covers fetching and staging; nothing is visible until the commit below.
            async with fetch_deadline:
                if kind_name is not None and write_mode == "append":
                    await _adopt_committed_resume(
                        deps.pool, tenant_id=tenant_id, dataset_name=dataset_name, kind_name=kind_name
                    )
                source_rows, commit_cursor = await read()
                async for batch in _row_batches(source_rows, INGEST_BATCH_ROWS):
                    await asyncio.to_thread(writer.add, batch)
        except TimeoutError as exc:
            if not fetch_deadline.expired():
                raise
            raise HolonError.unavailable(
                'SourceFetchTimeout',
                f"source {dataset_name!r} did not finish fetching within {fetch_timeout:g}s",
                dataset_name=dataset_name,
            ) from exc
        except source_registry_base.SourceFetchError as exc:
            raise HolonError.invalid_argument('DatasetValidationFailed', str(exc)) from exc
        except ConnectorSafetyError as exc:
            raise HolonError.invalid_argument('DatasetValidationFailed', str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            raise HolonError.invalid_argument('SourceHttpError', f"source returned {exc.response.status_code}: {exc.response.text[:300]}") from exc
        except httpx.RequestError as exc:
            raise HolonError.invalid_argument('SourceUnreachable', f"could not reach the source: {exc}") from exc
        resume = getattr(commit_cursor, "resume", None)
        stamped = resume() if callable(resume) else None

        def _commit():
            if stamped is not None:
                writer.note_resume(stamped[0], stamped[1])
            return writer.commit()

        result = await asyncio.to_thread(_commit)
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
    except Exception as exc:
        await _record_sync_failure(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            connector_urn=connector_urn,
            dataset_name=dataset_name,
            actor=actor,
            started_at=started_at,
            exc=exc,
        )
        raise

