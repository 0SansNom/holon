"""Sync execution, scheduler, pipeline runs, Kafka stream tasks.

HTTP handlers live in `routers/`; this module is the shared worker path
the scheduler and those handlers both call.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import asyncpg
import httpx
from pydantic import BaseModel

from holon_common import (
    EventActor,
    EventEnvelope,
    HolonError,
    Principal,
    build_urn,
    issue_token,
    outbox,
)
from holon_common.audit import emit_audit

from . import (
    deps,
    generic_source_registry,
    iceberg_reader,
    iceberg_writer,
    object_source_registry,
    pipeline,
    plugin_registry,
    salesforce_source_registry,
    sql_source_registry,
    stream_connector,
)
from .deps import (
    CONNECTOR_URN_PIPELINE,
    ICEBERG_CONFIG,
    JWT_ACTIVE_KID,
    JWT_SECRET,
    JWT_SECRETS,
    KAFKA_BOOTSTRAP,
    KNOWLEDGE_URL,
    PIPELINE_FUNCTION_CALLER_URN,
    SCHEDULER_ACTOR_URN,
    SCHEDULER_POLL_SECONDS,
    STREAM_INGEST_URN,
    TENANT_ID,
    WORKFLOW_ENGINE_URN,
    WORKSPACE_ID,
    _SCHEDULER_LOCK_KEY,
    SyncResult,
)

logger = logging.getLogger("connectivity.scheduler")


class QuiesceRequest(BaseModel):
    quiesced: bool = True


class TransformStep(BaseModel):
    step_name: str
    input_dataset: str
    function_name: str
    output_dataset: str
    # Optional value type casts mapping column names to target types
    value_type_casts: Optional[dict[str, str]] = None


class CreatePipelineRequest(BaseModel):
    steps: list[TransformStep]
    workspace_id: Optional[str] = None


class SetPipelineScheduleRequest(BaseModel):
    schedule_interval_minutes: Optional[int] = None


class RegisterKafkaStreamRequest(BaseModel):
    name: str
    topic: str
    key_field: str
    dataset_name: str
    batch_interval_seconds: float = 5.0


class RegisterPluginRequest(BaseModel):
    entry_point: str


class SetPluginScheduleRequest(BaseModel):
    schedule_interval_minutes: Optional[int] = None


class RegisterConnectionRequest(BaseModel):
    name: str
    auth_type: str = "header"
    auth_header_name: Optional[str] = None
    # Optional; if omitted on edit, existing secret is retained
    auth_header_value: Optional[str] = None
    oauth2_token_url: Optional[str] = None
    oauth2_client_id: Optional[str] = None
    # Optional; if omitted on edit, existing secret is retained
    oauth2_client_secret: Optional[str] = None
    oauth2_scope: Optional[str] = None
    secret_ref: Optional[str] = None


class RegisterSourceRequest(BaseModel):
    name: str
    base_url: str
    workspace_id: Optional[str] = None
    auth_header_name: Optional[str] = None
    auth_header_value: Optional[str] = None
    record_path: Optional[str] = None
    next_page_path: Optional[str] = None
    connection_name: Optional[str] = None
    schedule_interval_minutes: Optional[int] = None
    cursor_property: Optional[str] = None
    incremental_param: Optional[str] = None


class RegisterSqlConnectionRequest(BaseModel):
    name: str
    host: str
    dialect: str = "postgres"
    # Optional; defaults to the dialect's standard port when omitted
    port: Optional[int] = None
    database: str
    username: str
    # Optional; if omitted on edit, existing secret is retained
    password: Optional[str] = None
    secret_ref: Optional[str] = None


class RegisterSqlSourceRequest(BaseModel):
    name: str
    connection_name: str
    workspace_id: Optional[str] = None
    table_name: Optional[str] = None
    query: Optional[str] = None
    schedule_interval_minutes: Optional[int] = None
    cursor_property: Optional[str] = None


class RegisterObjectConnectionRequest(BaseModel):
    name: str
    access_key_id: str
    kind: str = "s3"
    endpoint: Optional[str] = None
    region: str = "us-east-1"
    path_style: bool = True
    # Optional; if omitted on edit, existing secret is retained
    secret_access_key: Optional[str] = None
    secret_ref: Optional[str] = None


class RegisterObjectSourceRequest(BaseModel):
    name: str
    connection_name: str
    bucket: str
    format: str
    workspace_id: Optional[str] = None
    object_key: Optional[str] = None
    key_prefix: Optional[str] = None
    incremental: bool = False
    schedule_interval_minutes: Optional[int] = None


class RegisterSalesforceConnectionRequest(BaseModel):
    name: str
    client_id: str
    login_url: Optional[str] = None
    # Optional; if omitted on edit, existing secret is retained
    client_secret: Optional[str] = None
    secret_ref: Optional[str] = None


class RegisterSalesforceSourceRequest(BaseModel):
    name: str
    connection_name: str
    soql: str
    workspace_id: Optional[str] = None
    api_version: str = "v59.0"
    cursor_property: Optional[str] = None
    schedule_interval_minutes: Optional[int] = None


class CloseAccountRequest(BaseModel):
    reason: str


class RegisterWriteTargetRequest(BaseModel):
    dataset_name: str
    table_name: str
    id_column: str
    allowed_properties: dict[str, str]


class WriteSourceRequest(BaseModel):
    edits: dict[str, object]


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
        correlation_id=event_id,
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
        read = plugin.fetch
    else:
        source = await generic_source_registry.get_source(deps.pool, tenant_id, dataset_name)
        if source is not None:
            if source["status"] != "active":
                raise HolonError.conflict(
                    "SourceDisabled",
                    f"source {dataset_name!r} is disabled — enable it first",
                    dataset_name=dataset_name,
                )
            connector_urn = build_urn(tenant_id, "global", "connector", f"generic-rest-{dataset_name}")
            read = functools.partial(generic_source_registry.fetch_for_dataset, deps.pool, tenant_id, dataset_name)
            # Use append mode for cursor-configured sources, overwrite for full sync
            if source["cursor_property"]:
                write_mode = "append"
        else:
            sql_source = await sql_source_registry.get_source(deps.pool, tenant_id, dataset_name)
            if sql_source is not None:
                if sql_source["status"] != "active":
                    raise HolonError.conflict(
                        "SourceDisabled",
                        f"source {dataset_name!r} is disabled — enable it first",
                        dataset_name=dataset_name,
                    )
                connector_urn = build_urn(tenant_id, "global", "connector", f"sql-{dataset_name}")
                read = functools.partial(sql_source_registry.fetch_for_dataset, deps.pool, tenant_id, dataset_name)
                if sql_source["cursor_property"]:
                    write_mode = "append"
            else:
                object_source = await object_source_registry.get_source(deps.pool, tenant_id, dataset_name)
                if object_source is not None:
                    if object_source["status"] != "active":
                        raise HolonError.conflict(
                            "SourceDisabled",
                            f"source {dataset_name!r} is disabled — enable it first",
                            dataset_name=dataset_name,
                        )
                    connector_urn = build_urn(tenant_id, "global", "connector", f"object-{dataset_name}")
                    read = functools.partial(
                        object_source_registry.fetch_for_dataset, deps.pool, tenant_id, dataset_name
                    )
                    if object_source["incremental"]:
                        write_mode = "append"
                else:
                    sf_source = await salesforce_source_registry.get_source(deps.pool, tenant_id, dataset_name)
                    if sf_source is None:
                        raise HolonError.not_found(
                            "DatasetNotFound", f"unknown dataset: {dataset_name}", dataset_name=dataset_name
                        )
                    if sf_source["status"] != "active":
                        raise HolonError.conflict(
                            "SourceDisabled",
                            f"source {dataset_name!r} is disabled — enable it first",
                            dataset_name=dataset_name,
                        )
                    connector_urn = build_urn(tenant_id, "global", "connector", f"salesforce-{dataset_name}")
                    read = functools.partial(
                        salesforce_source_registry.fetch_for_dataset, deps.pool, tenant_id, dataset_name
                    )
                    if sf_source["cursor_property"]:
                        write_mode = "append"

    started_at = datetime.now(timezone.utc)
    try:
        rows = await read()
    except generic_source_registry.SourceFetchError as exc:
        raise HolonError.invalid_argument('DatasetValidationFailed', str(exc)) from exc
    except sql_source_registry.SourceFetchError as exc:
        raise HolonError.invalid_argument('DatasetValidationFailed', str(exc)) from exc
    except object_source_registry.SourceFetchError as exc:
        raise HolonError.invalid_argument('DatasetValidationFailed', str(exc)) from exc
    except salesforce_source_registry.SourceFetchError as exc:
        raise HolonError.invalid_argument('DatasetValidationFailed', str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HolonError.invalid_argument('SourceHttpError', f"source returned {exc.response.status_code}: {exc.response.text[:300]}") from exc
    except httpx.RequestError as exc:
        raise HolonError.invalid_argument('SourceUnreachable', f"could not reach the source: {exc}") from exc
    result = await asyncio.to_thread(
        iceberg_writer.write_snapshot, rows, dataset_name, mode=write_mode, tenant_id=tenant_id, **ICEBERG_CONFIG
    )
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
                    sources = await generic_source_registry.list_all_scheduled_sources(pool)
                    for source in sources:
                        await _run_if_due(
                            dataset_name=source["name"],
                            tenant_id=source["tenant_id"],
                            workspace_id=source.get("workspace_id") or WORKSPACE_ID,
                            interval=timedelta(minutes=source["schedule_interval_minutes"]),
                        )
                    sql_sources = await sql_source_registry.list_all_scheduled_sources(pool)
                    for sql_source in sql_sources:
                        await _run_if_due(
                            dataset_name=sql_source["name"],
                            tenant_id=sql_source["tenant_id"],
                            workspace_id=sql_source.get("workspace_id") or WORKSPACE_ID,
                            interval=timedelta(minutes=sql_source["schedule_interval_minutes"]),
                        )
                    object_sources = await object_source_registry.list_all_scheduled_sources(pool)
                    for object_source in object_sources:
                        await _run_if_due(
                            dataset_name=object_source["name"],
                            tenant_id=object_source["tenant_id"],
                            workspace_id=object_source.get("workspace_id") or WORKSPACE_ID,
                            interval=timedelta(minutes=object_source["schedule_interval_minutes"]),
                        )
                    sf_sources = await salesforce_source_registry.list_all_scheduled_sources(pool)
                    for sf_source in sf_sources:
                        await _run_if_due(
                            dataset_name=sf_source["name"],
                            tenant_id=sf_source["tenant_id"],
                            workspace_id=sf_source.get("workspace_id") or WORKSPACE_ID,
                            interval=timedelta(minutes=sf_source["schedule_interval_minutes"]),
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


def _function_invocation_token() -> str:
    """Mint short-lived service token for internal function invocations."""
    principal = Principal(
        urn=PIPELINE_FUNCTION_CALLER_URN, type="service_account", tenant_id=TENANT_ID,
        display_name="Connectivity Pipeline Runner",
    )
    return issue_token(
        principal, JWT_SECRET, ttl_seconds=60, kid=JWT_ACTIVE_KID, secrets=JWT_SECRETS
    )


async def _latest_dataset_version_urn(dataset_name: str) -> Optional[str]:
    """Fetch latest dataset_version_urn from sync_run history."""
    dataset_urn = build_urn(TENANT_ID, WORKSPACE_ID, "dataset", dataset_name)
    row = await deps.pool.fetchrow(
        "SELECT dataset_version_urn FROM sync_run WHERE tenant_id = $1 AND dataset_urn = $2 ORDER BY id DESC LIMIT 1",
        TENANT_ID, dataset_urn,
    )
    return row["dataset_version_urn"] if row else None


async def _run_pipeline(name: str, *, actor: EventActor, tenant_id: str) -> dict:
    """Execute pipeline transform steps sequentially and finalize outputs."""
    definition = await pipeline.get_pipeline(deps.pool, tenant_id, name)
    if definition is None:
        raise HolonError.not_found('PipelineNotFound', f"unknown pipeline: {name}", name=name)

    run_started_at = datetime.now(timezone.utc)
    step_results: list[dict] = []

    try:
        for step in definition["steps"]:
            step_started_at = datetime.now(timezone.utc)
            source_dataset_version_urn = await _latest_dataset_version_urn(step["input_dataset"])

            input_rows = await asyncio.to_thread(
                iceberg_reader.read_table, step["input_dataset"], tenant_id=tenant_id, **ICEBERG_CONFIG
            )

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{KNOWLEDGE_URL}/api/holon/functions/{step['function_name']}/invoke",
                    json={"rows": input_rows},
                    headers={"Authorization": f"Bearer {_function_invocation_token()}"},
                )
            response.raise_for_status()
            output_rows = response.json()["rows"]

            casts = step.get("value_type_casts") or {}
            if casts:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    cast_response = await client.post(
                        f"{KNOWLEDGE_URL}/value-types/validate-casts",
                        json={"casts": casts, "rows": output_rows},
                        headers={"Authorization": f"Bearer {_function_invocation_token()}"},
                    )
                cast_response.raise_for_status()
                cast_result = cast_response.json()
                if not cast_result.get("ok", False):
                    sample = cast_result.get("errors") or []
                    raise ValueError(
                        f"step {step['step_name']!r} value_type_casts failed "
                        f"({cast_result.get('error_count', len(sample))} errors): {sample[:3]}"
                    )

            write_result = await asyncio.to_thread(
                iceberg_writer.write_snapshot, output_rows, step["output_dataset"], tenant_id=tenant_id, **ICEBERG_CONFIG
            )
            step_finished_at = datetime.now(timezone.utc)

            sync_result = await _finalize_sync(
                connector_urn=CONNECTOR_URN_PIPELINE,
                dataset_name=step["output_dataset"],
                result=write_result,
                started_at=step_started_at,
                finished_at=step_finished_at,
                actor=actor,
                source_dataset_version_urn=source_dataset_version_urn,
            )
            step_results.append({"step_name": step["step_name"], **sync_result.model_dump()})
    except Exception as exc:
        run_finished_at = datetime.now(timezone.utc)
        await pipeline.record_run(
            deps.pool,
            tenant_id=tenant_id,
            pipeline_name=name,
            status="failed",
            started_at=run_started_at,
            finished_at=run_finished_at,
            step_results=step_results,
            error=str(exc),
        )
        raise HolonError.invalid_argument('PipelineRunFailed', f"pipeline run failed: {exc}") from exc

    run_finished_at = datetime.now(timezone.utc)
    return await pipeline.record_run(
        deps.pool,
        tenant_id=tenant_id,
        pipeline_name=name,
        status="succeeded",
        started_at=run_started_at,
        finished_at=run_finished_at,
        step_results=step_results,
    )


def _kafka_stream_task_key(tenant_id: str, name: str) -> tuple[str, str]:
    return (tenant_id, name)


def _spawn_kafka_stream_task(source: dict) -> None:
    """Start or restart a Kafka stream consumer task."""
    key = _kafka_stream_task_key(source["tenant_id"], source["name"])
    existing = deps.kafka_stream_tasks.get(key)
    if existing is not None and not existing.done():
        existing.cancel()
    connector_urn = build_urn(source["tenant_id"], "global", "connector", f"kafka-stream-{source['name']}")
    deps.kafka_stream_tasks[key] = asyncio.create_task(
        stream_connector.consume_stream_forever(
            source=source,
            kafka_bootstrap=KAFKA_BOOTSTRAP,
            iceberg_config=ICEBERG_CONFIG,
            connector_urn=connector_urn,
            pool=deps.pool,
            record_sync=functools.partial(
                _finalize_sync,
                actor=EventActor(type="service_account", urn=STREAM_INGEST_URN, on_behalf_of=None),
            ),
        )
    )


def _cancel_kafka_stream_task(tenant_id: str, name: str) -> None:
    key = _kafka_stream_task_key(tenant_id, name)
    task = deps.kafka_stream_tasks.pop(key, None)
    if task is not None and not task.done():
        task.cancel()


def _kafka_stream_not_found(name: str) -> HolonError:
    return HolonError.not_found("KafkaStreamNotFound", f"no Kafka stream registered as {name!r}", name=name)


def _plugin_not_found(name: str) -> HolonError:
    return HolonError.not_found("PluginNotFound", f"no plugin registered as {name!r}", name=name)


def _source_not_found(name: str) -> HolonError:
    return HolonError.not_found("SourceNotFound", f"no source registered as {name!r}", name=name)


def _require_workflow_engine(principal: Principal) -> None:
    """Verify caller is the Automation Workflow Engine service account."""
    if principal.type != "service_account" or principal.urn != WORKFLOW_ENGINE_URN:
        raise HolonError.forbidden(
            "AutomationOnlyEndpoint",
            "close-account is restricted to Automation's Workflow Engine — use the approval flow",
        )

