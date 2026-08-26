"""Connectivity Platform — Connector execution and ingestion.

Executes registered connectors and lands data in the Iceberg raw zone.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

import asyncpg
import httpx
from fastapi import Depends, FastAPI, Header, Query
from pydantic import BaseModel

from holon_common import (
    HolonError,
    EventActor,
    EventEnvelope,
    EventProducer,
    Principal,
    active_jwt,
    assert_production_posture,
    build_urn,
    configure_json_logging,
    create_pool,
    install_error_handlers,
    instrument_cors,
    instrument_metrics,
    instrument_tracing,
    issue_token,
    make_principal_dependency,
    outbox,
    require_tenant_match,
    retry_with_backoff,
    run_migrations,
)
from holon_common.audit import clear_durable_audit_hooks, emit_audit
from holon_common.audit_store import (
    ensure_schema as ensure_audit_schema,
    install_durable_audit,
    list_events_page,
)
from holon_common.authz import PermissionClient
from holon_common.principal_status import (
    consume_identity_auth_events,
    hydrate_revocation_snapshot,
    make_principal_status_consumer,
)
from holon_common.readiness import (
    check_iceberg_catalog,
    check_kafka_producer,
    check_opa,
    check_postgres,
    check_spicedb,
    report_ready,
)

from . import (
    generic_source_registry,
    iceberg_reader,
    iceberg_writer,
    kafka_stream_registry,
    object_source_registry,
    pipeline,
    plugin_registry,
    sql_source_registry,
    stream_connector,
    write_target_registry,
)

SERVICE_NAME = "connectivity-platform"
configure_json_logging(SERVICE_NAME)
logger = logging.getLogger("connectivity.scheduler")

TENANT_ID = os.environ["HOLON_TENANT_ID"]
WORKSPACE_ID = os.environ["HOLON_WORKSPACE_ID"]
JWT_SECRET, JWT_ACTIVE_KID, JWT_SECRETS = active_jwt()
DB_URL = os.environ["HOLON_DB_URL"]
KNOWLEDGE_URL = os.environ["HOLON_KNOWLEDGE_URL"]
KAFKA_BOOTSTRAP = os.environ["HOLON_KAFKA_BOOTSTRAP"]
OTLP_ENDPOINT = os.environ.get("HOLON_OTLP_ENDPOINT", "")
SPICEDB_URL = os.environ["HOLON_SPICEDB_URL"]
SPICEDB_PRESHARED_KEY = os.environ["HOLON_SPICEDB_PRESHARED_KEY"]
OPA_URL = os.environ["HOLON_OPA_URL"]


def _source_db_url() -> str:
    url = (os.environ.get("HOLON_SOURCE_DB_URL") or "").strip()
    if not url:
        raise HolonError.unavailable('Unavailable', "HOLON_SOURCE_DB_URL is not configured")
    return url

ICEBERG_CONFIG = dict(
    catalog_uri=os.environ["HOLON_ICEBERG_CATALOG_URI"],
    warehouse=os.environ["HOLON_ICEBERG_WAREHOUSE"],
    s3_endpoint=os.environ["HOLON_S3_ENDPOINT"],
    access_key=os.environ["AWS_ACCESS_KEY_ID"],
    secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    region=os.environ["AWS_REGION"],
)

CONNECTOR_URN_PIPELINE = build_urn(TENANT_ID, "global", "connector", "pipeline-transform")

STREAM_INGEST_URN = build_urn(TENANT_ID, "global", "service-account", "connectivity-stream-ingest")
SCHEDULER_ACTOR_URN = build_urn(TENANT_ID, "global", "service-account", "connectivity-scheduler")
SCHEDULER_POLL_SECONDS = 60


async def _reserved_dataset_names(pool: asyncpg.Pool) -> frozenset[str]:
    """Returns dataset names owned by active Kafka streams."""
    active = await kafka_stream_registry.list_all_active(pool)
    return frozenset(source["dataset_name"] for source in active)

_DDL = """
CREATE TABLE IF NOT EXISTS sync_run (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    connector_urn TEXT NOT NULL,
    dataset_urn TEXT NOT NULL,
    dataset_version_urn TEXT NOT NULL,
    iceberg_namespace TEXT NOT NULL,
    iceberg_table TEXT NOT NULL,
    snapshot_id BIGINT NOT NULL,
    row_count INTEGER NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL
);

-- Cluster-wide flags (quiesce) so multi-replica Connectivity shares state.
CREATE TABLE IF NOT EXISTS connectivity_runtime (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# Session-level advisory lock: only one replica runs the scheduler loop work.
_SCHEDULER_LOCK_KEY = 826_450_001


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_production_posture(service_name=SERVICE_NAME)
    app.state.pool = await create_pool(DB_URL)
    async with app.state.pool.acquire() as conn:
        await conn.execute(_DDL)
        await ensure_audit_schema(conn)
        await outbox.ensure_schema(conn)
        await plugin_registry.ensure_schema(conn)
        await generic_source_registry.ensure_schema(conn)
        await sql_source_registry.ensure_schema(conn)
        await object_source_registry.ensure_schema(conn)
        await pipeline.ensure_schema(conn)
        await write_target_registry.ensure_schema(conn)
        await stream_connector.ensure_schema(conn)
    await run_migrations(app.state.pool, Path(__file__).parent / "migrations")

    clear_durable_audit_hooks()
    install_durable_audit(app.state.pool)

    app.state.authz = PermissionClient(SPICEDB_URL, SPICEDB_PRESHARED_KEY, OPA_URL)

    app.state.producer = EventProducer(KAFKA_BOOTSTRAP)
    await app.state.producer.start()
    relay_task = asyncio.create_task(outbox.relay_forever(app.state.pool, app.state.producer, dlq_producer=app.state.producer))

    # Spawn consumer tasks for active Kafka streams
    app.state.kafka_stream_tasks = {}
    for source in await kafka_stream_registry.list_all_active(app.state.pool):
        _spawn_kafka_stream_task(source)

    scheduler_task = asyncio.create_task(run_scheduler_forever(app.state.pool))

    status_consumer = make_principal_status_consumer(
        KAFKA_BOOTSTRAP, service_name=SERVICE_NAME, dlq_producer=app.state.producer
    )
    status_task = asyncio.create_task(consume_identity_auth_events(status_consumer, authz=app.state.authz))
    await retry_with_backoff(hydrate_revocation_snapshot, what="identity revocation snapshot")

    yield

    status_task.cancel()
    scheduler_task.cancel()
    for task in app.state.kafka_stream_tasks.values():
        task.cancel()
    relay_task.cancel()
    await status_consumer.stop()
    await app.state.producer.stop()
    await app.state.authz.aclose()
    await app.state.pool.close()


app = FastAPI(title="Holon — Connectivity Platform", lifespan=lifespan)
instrument_cors(app)  # Sources page (Experience) calls this service directly from the browser now
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
install_error_handlers(app, service_name=SERVICE_NAME)
current_principal = make_principal_dependency(JWT_SECRET, secrets=JWT_SECRETS)


class SyncRequest(BaseModel):
    dataset: str = "customers"
    # Target workspace for dataset URNs (or source workspace_id)
    workspace_id: Optional[str] = None


class SyncResult(BaseModel):
    dataset_urn: str
    dataset_version_urn: str
    snapshot_id: int
    row_count: int
    location: str


def _resolve_workspace(
    *,
    explicit: Optional[str] = None,
    workspace_id: Optional[str] = None,
    x_holon_workspace_id: Optional[str] = None,
) -> str:
    return explicit or workspace_id or x_holon_workspace_id or WORKSPACE_ID


def _workspace_urn(tenant_id: str, workspace_id: str) -> str:
    return build_urn(tenant_id, "global", "workspace", workspace_id)


async def _authorize_workspace(
    principal: Principal, permission: str, *, workspace_id: Optional[str] = None
) -> None:
    """Authorize workspace permissions via ReBAC."""
    urn = _workspace_urn(principal.tenant_id, workspace_id or WORKSPACE_ID)
    decision = await app.state.authz.authorize(
        principal, resource_type="workspace", resource_urn=urn, permission=permission
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/live")
async def live() -> dict:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict:
    return await report_ready(
        [
            check_postgres(app.state.pool),
            check_spicedb(SPICEDB_URL, SPICEDB_PRESHARED_KEY),
            check_opa(OPA_URL),
            check_kafka_producer(app.state.producer),
            check_iceberg_catalog(ICEBERG_CONFIG["catalog_uri"], ICEBERG_CONFIG["warehouse"]),
        ],
        extra={"quiesced": await _is_quiesced(app.state.pool)},
    )


@app.get("/audit-events")
async def list_connectivity_audit_events(
    principal: Principal = Depends(current_principal),
    category: Optional[str] = None,
    action: Optional[str] = None,
    actor: Optional[str] = None,
    outcome: Optional[str] = None,
    pageSize: Optional[int] = None,
    pageToken: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> dict:
    """Durable Connectivity audit (syncs, plugins, sources, quiesce)."""
    await _authorize_workspace(principal, "approve", workspace_id=workspace_id)
    return await list_events_page(
        app.state.pool,
        principal.tenant_id,
        category=category,
        action=action,
        actor_urn=actor,
        outcome=outcome,
        page_size=50 if pageSize is None else pageSize,
        page_token=pageToken,
    )


class QuiesceRequest(BaseModel):
    quiesced: bool = True


@app.post("/admin/quiesce")
async def admin_quiesce(body: QuiesceRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Toggle scheduled ingestion quiesce status across replicas."""
    require_tenant_match(principal, TENANT_ID)  # bootstrap admins only for instance quiesce
    await app.state.pool.execute(
        """
        INSERT INTO connectivity_runtime (key, value, updated_at) VALUES ('quiesced', $1, now())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """,
        "true" if body.quiesced else "false",
    )
    emit_audit(
        category="access",
        action="connectivity.quiesce",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="connectivity_runtime",
        resource_urn=build_urn(principal.tenant_id, "global", "connectivity-runtime", "quiesced"),
        extra={"quiesced": body.quiesced},
    )
    return {"quiesced": body.quiesced}


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

    async with app.state.pool.acquire() as conn:
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
    plugin = await plugin_registry.load_active_plugin_for_dataset(app.state.pool, dataset_name, tenant_id)
    if plugin is not None:
        local_name = plugin.manifest.connector_local_name or f"plugin-{plugin.manifest.name}"
        connector_urn = build_urn(tenant_id, "global", "connector", local_name)
        read = plugin.fetch
    else:
        source = await generic_source_registry.get_source(app.state.pool, tenant_id, dataset_name)
        if source is not None:
            if source["status"] != "active":
                raise HolonError.conflict(
                    "SourceDisabled",
                    f"source {dataset_name!r} is disabled — enable it first",
                    dataset_name=dataset_name,
                )
            connector_urn = build_urn(tenant_id, "global", "connector", f"generic-rest-{dataset_name}")
            read = functools.partial(generic_source_registry.fetch_for_dataset, app.state.pool, tenant_id, dataset_name)
            # Use append mode for cursor-configured sources, overwrite for full sync
            if source["cursor_property"]:
                write_mode = "append"
        else:
            sql_source = await sql_source_registry.get_source(app.state.pool, tenant_id, dataset_name)
            if sql_source is not None:
                if sql_source["status"] != "active":
                    raise HolonError.conflict(
                        "SourceDisabled",
                        f"source {dataset_name!r} is disabled — enable it first",
                        dataset_name=dataset_name,
                    )
                connector_urn = build_urn(tenant_id, "global", "connector", f"sql-{dataset_name}")
                read = functools.partial(sql_source_registry.fetch_for_dataset, app.state.pool, tenant_id, dataset_name)
                if sql_source["cursor_property"]:
                    write_mode = "append"
            else:
                object_source = await object_source_registry.get_source(app.state.pool, tenant_id, dataset_name)
                if object_source is None:
                    raise HolonError.not_found('DatasetNotFound', f"unknown dataset: {dataset_name}", dataset_name=dataset_name)
                if object_source["status"] != "active":
                    raise HolonError.conflict(
                        "SourceDisabled",
                        f"source {dataset_name!r} is disabled — enable it first",
                        dataset_name=dataset_name,
                    )
                connector_urn = build_urn(tenant_id, "global", "connector", f"object-{dataset_name}")
                read = functools.partial(object_source_registry.fetch_for_dataset, app.state.pool, tenant_id, dataset_name)
                if object_source["incremental"]:
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


@app.post("/sync", response_model=SyncResult)
async def run_sync(
    request: SyncRequest = SyncRequest(),
    principal: Principal = Depends(current_principal),
    workspace_id: Optional[str] = Query(None, alias="workspaceId"),
    x_holon_workspace_id: Optional[str] = Header(None, alias="X-Holon-Workspace-Id"),
) -> SyncResult:
    target_workspace = _resolve_workspace(
        explicit=request.workspace_id,
        workspace_id=workspace_id,
        x_holon_workspace_id=x_holon_workspace_id,
    )
    source = await generic_source_registry.get_source(app.state.pool, principal.tenant_id, request.dataset)
    if source is not None and source.get("workspace_id"):
        stored = source["workspace_id"]
        if request.workspace_id and request.workspace_id != stored:
            raise HolonError.invalid_argument(
                "SourceWorkspaceMismatch",
                (
                    f"source {request.dataset!r} is bound to workspace {stored!r}; "
                    f"got {request.workspace_id!r}"
                ),
                dataset=request.dataset,
                expected_workspace_id=stored,
                got_workspace_id=request.workspace_id,
            )
        target_workspace = stored
    await _authorize_workspace(principal, "write", workspace_id=target_workspace)
    actor = EventActor(type=principal.type, urn=principal.urn, on_behalf_of=principal.on_behalf_of)
    return await _run_sync_for_dataset(
        request.dataset, actor=actor, tenant_id=principal.tenant_id, workspace_id=target_workspace
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
            "SELECT finished_at FROM pipeline_run WHERE pipeline_name = $1 AND status = 'succeeded' "
            "ORDER BY id DESC LIMIT 1",
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


@app.get("/syncs")
async def list_syncs(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    rows = await app.state.pool.fetch(
        "SELECT * FROM sync_run WHERE tenant_id = $1 ORDER BY id DESC", principal.tenant_id
    )
    return [dict(row) for row in rows]


PIPELINE_FUNCTION_CALLER_URN = build_urn(TENANT_ID, "global", "service-account", "connectivity-pipeline-runner")


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
    row = await app.state.pool.fetchrow(
        "SELECT dataset_version_urn FROM sync_run WHERE tenant_id = $1 AND dataset_urn = $2 ORDER BY id DESC LIMIT 1",
        TENANT_ID, dataset_urn,
    )
    return row["dataset_version_urn"] if row else None


class TransformStep(BaseModel):
    step_name: str
    input_dataset: str
    function_name: str
    output_dataset: str
    # Optional value type casts mapping column names to target types
    value_type_casts: Optional[dict[str, str]] = None


class CreatePipelineRequest(BaseModel):
    steps: list[TransformStep]


@app.post("/pipelines/{name}", status_code=201)
async def create_pipeline(
    name: str, request: CreatePipelineRequest, principal: Principal = Depends(current_principal)
) -> dict:
    await _authorize_workspace(principal, "write")
    try:
        return await pipeline.create_pipeline(
            app.state.pool,
            tenant_id=principal.tenant_id,
            name=name,
            steps=[step.model_dump() for step in request.steps],
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('PipelineValidationFailed', str(exc)) from exc


@app.get("/pipelines")
async def list_pipelines(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await pipeline.list_pipelines(app.state.pool, principal.tenant_id)


@app.get("/pipelines/{name}")
async def get_pipeline(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "read")
    definition = await pipeline.get_pipeline(app.state.pool, name)
    if definition is None:
        raise HolonError.not_found('PipelineNotFound', f"unknown pipeline: {name}", name=name)
    return definition


@app.delete("/pipelines/{name}")
async def delete_pipeline(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    deleted = await pipeline.delete_pipeline(
        app.state.pool, tenant_id=principal.tenant_id, name=name
    )
    if not deleted:
        raise HolonError.not_found('PipelineNotFound', f"unknown pipeline: {name}", name=name)
    return {"deleted": name}


@app.get("/pipelines/{name}/runs")
async def list_pipeline_runs(name: str, principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await pipeline.list_runs(app.state.pool, principal.tenant_id, name)


async def _run_pipeline(name: str, *, actor: EventActor, tenant_id: str) -> dict:
    """Execute pipeline transform steps sequentially and finalize outputs."""
    definition = await pipeline.get_pipeline(app.state.pool, name)
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
            app.state.pool,
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
        app.state.pool,
        tenant_id=tenant_id,
        pipeline_name=name,
        status="succeeded",
        started_at=run_started_at,
        finished_at=run_finished_at,
        step_results=step_results,
    )


@app.post("/pipelines/{name}/run")
async def run_pipeline(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    actor = EventActor(type=principal.type, urn=principal.urn, on_behalf_of=principal.on_behalf_of)
    return await _run_pipeline(name, actor=actor, tenant_id=principal.tenant_id)


class SetPipelineScheduleRequest(BaseModel):
    schedule_interval_minutes: Optional[int] = None


@app.post("/pipelines/{name}/schedule")
async def set_pipeline_schedule(name: str, body: SetPipelineScheduleRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Set or clear background execution schedule for a pipeline."""
    await _authorize_workspace(principal, "write")
    if body.schedule_interval_minutes is not None and body.schedule_interval_minutes <= 0:
        raise HolonError.invalid_argument(
            "InvalidSchedule", "schedule_interval_minutes must be a positive number of minutes"
        )
    if await pipeline.get_pipeline(app.state.pool, name) is None:
        raise HolonError.not_found('PipelineNotFound', f"unknown pipeline: {name}", name=name)
    result = await pipeline.set_pipeline_schedule(app.state.pool, name, body.schedule_interval_minutes)
    emit_audit(
        category="access",
        action="connectivity.pipeline.schedule_updated",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="pipeline",
        resource_urn=build_urn(principal.tenant_id, "global", "pipeline", name),
        extra={"schedule_interval_minutes": body.schedule_interval_minutes},
    )
    return result


def _kafka_stream_task_key(tenant_id: str, name: str) -> tuple[str, str]:
    return (tenant_id, name)


def _spawn_kafka_stream_task(source: dict) -> None:
    """Start or restart a Kafka stream consumer task."""
    key = _kafka_stream_task_key(source["tenant_id"], source["name"])
    existing = app.state.kafka_stream_tasks.get(key)
    if existing is not None and not existing.done():
        existing.cancel()
    connector_urn = build_urn(source["tenant_id"], "global", "connector", f"kafka-stream-{source['name']}")
    app.state.kafka_stream_tasks[key] = asyncio.create_task(
        stream_connector.consume_stream_forever(
            source=source,
            kafka_bootstrap=KAFKA_BOOTSTRAP,
            iceberg_config=ICEBERG_CONFIG,
            connector_urn=connector_urn,
            pool=app.state.pool,
            record_sync=functools.partial(
                _finalize_sync,
                actor=EventActor(type="service_account", urn=STREAM_INGEST_URN, on_behalf_of=None),
            ),
        )
    )


def _cancel_kafka_stream_task(tenant_id: str, name: str) -> None:
    key = _kafka_stream_task_key(tenant_id, name)
    task = app.state.kafka_stream_tasks.pop(key, None)
    if task is not None and not task.done():
        task.cancel()


class RegisterKafkaStreamRequest(BaseModel):
    name: str
    topic: str
    key_field: str
    dataset_name: str
    batch_interval_seconds: float = 5.0


@app.post("/kafka-streams", status_code=201)
async def register_kafka_stream(body: RegisterKafkaStreamRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Register a new Kafka stream source."""
    await _authorize_workspace(principal, "write")
    if body.batch_interval_seconds <= 0:
        raise HolonError.invalid_argument("InvalidBatchInterval", "batch_interval_seconds must be positive")
    try:
        source = await kafka_stream_registry.register_source(
            app.state.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            topic=body.topic,
            key_field=body.key_field,
            dataset_name=body.dataset_name,
            batch_interval_seconds=body.batch_interval_seconds,
            created_by_urn=principal.urn,
        )
    except kafka_stream_registry.KafkaStreamConflictError as exc:
        raise HolonError.conflict('KafkaStreamConflict', str(exc)) from exc
    emit_audit(
        category="access",
        action="connectivity.kafka_stream.registered",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="kafka_stream",
        resource_urn=build_urn(principal.tenant_id, "global", "kafka-stream", body.name),
        extra={"topic": body.topic, "dataset_name": body.dataset_name},
    )
    _spawn_kafka_stream_task(source)
    return source


@app.get("/kafka-streams")
async def list_kafka_streams(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await kafka_stream_registry.list_sources(app.state.pool, principal.tenant_id)


def _kafka_stream_not_found(name: str) -> HolonError:
    return HolonError.not_found("KafkaStreamNotFound", f"no Kafka stream registered as {name!r}", name=name)


@app.get("/kafka-streams/{name}")
async def get_kafka_stream(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "read")
    source = await kafka_stream_registry.get_source(app.state.pool, principal.tenant_id, name)
    if source is None:
        raise _kafka_stream_not_found(name)
    return source


@app.post("/kafka-streams/{name}/disable")
async def disable_kafka_stream(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    source = await kafka_stream_registry.get_source(app.state.pool, principal.tenant_id, name)
    if source is None:
        raise _kafka_stream_not_found(name)
    result = await kafka_stream_registry.set_status(app.state.pool, principal.tenant_id, name, "disabled")
    _cancel_kafka_stream_task(principal.tenant_id, name)
    return result


@app.post("/kafka-streams/{name}/enable")
async def enable_kafka_stream(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    source = await kafka_stream_registry.get_source(app.state.pool, principal.tenant_id, name)
    if source is None:
        raise _kafka_stream_not_found(name)
    result = await kafka_stream_registry.set_status(app.state.pool, principal.tenant_id, name, "active")
    _spawn_kafka_stream_task(result)
    return result


@app.delete("/kafka-streams/{name}")
async def delete_kafka_stream(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    deleted = await kafka_stream_registry.delete_source(app.state.pool, principal.tenant_id, name)
    if not deleted:
        raise _kafka_stream_not_found(name)
    _cancel_kafka_stream_task(principal.tenant_id, name)
    return {"deleted": name}


class RegisterPluginRequest(BaseModel):
    entry_point: str


@app.get("/plugins")
async def list_plugins(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await plugin_registry.list_plugin_registrations(app.state.pool, principal.tenant_id)


@app.post("/plugins")
async def register_plugin(body: RegisterPluginRequest, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    """Register a connector plugin by entry point."""
    try:
        registration = await plugin_registry.register_plugin(
            app.state.pool,
            entry_point=body.entry_point,
            tenant_id=principal.tenant_id,
            reserved_dataset_names=await _reserved_dataset_names(app.state.pool),
        )
    except plugin_registry.PluginConflictError as exc:
        raise HolonError.conflict('PluginConflict', str(exc)) from exc
    emit_audit(
        category="access",
        action="connectivity.plugin.registered",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="connector_plugin",
        resource_urn=build_urn(principal.tenant_id, "global", "connector-plugin", registration["name"]),
        extra={"entry_point": body.entry_point},
    )
    return registration


def _plugin_not_found(name: str) -> HolonError:
    return HolonError.not_found("PluginNotFound", f"no plugin registered as {name!r}", name=name)


@app.get("/plugins/{name}")
async def get_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "read")
    registration = await plugin_registry.get_plugin_registration(app.state.pool, name, principal.tenant_id)
    if registration is None:
        raise _plugin_not_found(name)
    return registration


@app.post("/plugins/{name}/disable")
async def disable_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    registration = await plugin_registry.get_plugin_registration(app.state.pool, name, principal.tenant_id)
    if registration is None:
        raise _plugin_not_found(name)
    if registration.get("tenant_id") != principal.tenant_id:
        raise _plugin_not_found(name)
    result = await plugin_registry.set_plugin_status(
        app.state.pool, name, "disabled", tenant_id=principal.tenant_id
    )
    if result is None:
        raise _plugin_not_found(name)
    emit_audit(
        category="access",
        action="connectivity.plugin.disabled",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="connector_plugin",
        resource_urn=build_urn(principal.tenant_id, "global", "connector-plugin", name),
    )
    return result


@app.post("/plugins/{name}/enable")
async def enable_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    registration = await plugin_registry.get_plugin_registration(app.state.pool, name, principal.tenant_id)
    if registration is None:
        raise _plugin_not_found(name)
    if registration.get("tenant_id") != principal.tenant_id:
        raise _plugin_not_found(name)
    result = await plugin_registry.set_plugin_status(
        app.state.pool, name, "active", tenant_id=principal.tenant_id
    )
    if result is None:
        raise _plugin_not_found(name)
    emit_audit(
        category="access",
        action="connectivity.plugin.enabled",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="connector_plugin",
        resource_urn=build_urn(principal.tenant_id, "global", "connector-plugin", name),
    )
    return result


class SetPluginScheduleRequest(BaseModel):
    schedule_interval_minutes: Optional[int] = None


@app.post("/plugins/{name}/schedule")
async def set_plugin_schedule(name: str, body: SetPluginScheduleRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Set or clear background execution schedule for a plugin."""
    await _authorize_workspace(principal, "write")
    if body.schedule_interval_minutes is not None and body.schedule_interval_minutes <= 0:
        raise HolonError.invalid_argument(
            "InvalidSchedule", "schedule_interval_minutes must be a positive number of minutes"
        )
    registration = await plugin_registry.get_plugin_registration(app.state.pool, name, principal.tenant_id)
    if registration is None or registration.get("tenant_id") != principal.tenant_id:
        raise _plugin_not_found(name)
    result = await plugin_registry.set_plugin_schedule(
        app.state.pool, name, body.schedule_interval_minutes, tenant_id=principal.tenant_id
    )
    if result is None:
        raise _plugin_not_found(name)
    emit_audit(
        category="access",
        action="connectivity.plugin.schedule_updated",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="connector_plugin",
        resource_urn=build_urn(principal.tenant_id, "global", "connector-plugin", name),
        extra={"schedule_interval_minutes": body.schedule_interval_minutes},
    )
    return result


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


@app.post("/connections")
async def register_connection(body: RegisterConnectionRequest, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    """Register or update a REST connection credential."""
    try:
        return await generic_source_registry.register_connection(
            app.state.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            auth_type=body.auth_type,
            auth_header_name=body.auth_header_name,
            auth_header_value=body.auth_header_value,
            oauth2_token_url=body.oauth2_token_url,
            oauth2_client_id=body.oauth2_client_id,
            oauth2_client_secret=body.oauth2_client_secret,
            oauth2_scope=body.oauth2_scope,
            secret_ref=body.secret_ref,
            created_by_urn=principal.urn,
        )
    except generic_source_registry.SourceConfigError as exc:
        raise HolonError.invalid_argument('ConnectionValidationFailed', str(exc)) from exc


@app.get("/connections")
async def list_connections(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await generic_source_registry.list_connections(app.state.pool, principal.tenant_id)


@app.delete("/connections/{name}")
async def delete_connection(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    if await generic_source_registry.get_connection(app.state.pool, principal.tenant_id, name) is None:
        raise HolonError.not_found('ConnectionNotFound', f"no connection registered as {name!r}", name=name)
    try:
        await generic_source_registry.delete_connection(app.state.pool, principal.tenant_id, name)
    except generic_source_registry.ConnectionInUseError as exc:
        raise HolonError.conflict('ConnectionConflict', str(exc)) from exc
    return {"deleted": name}


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


@app.post("/sources")
async def register_source(
    body: RegisterSourceRequest,
    principal: Principal = Depends(current_principal),
    workspace_id: Optional[str] = Query(None, alias="workspaceId"),
    x_holon_workspace_id: Optional[str] = Header(None, alias="X-Holon-Workspace-Id"),
) -> dict:
    """Register a new REST API source."""
    target_workspace = _resolve_workspace(
        explicit=body.workspace_id,
        workspace_id=workspace_id,
        x_holon_workspace_id=x_holon_workspace_id,
    )
    await _authorize_workspace(principal, "write", workspace_id=target_workspace)
    try:
        registration = await generic_source_registry.register_source(
            app.state.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            base_url=body.base_url,
            created_by_urn=principal.urn,
            workspace_id=target_workspace,
            auth_header_name=body.auth_header_name,
            auth_header_value=body.auth_header_value,
            record_path=body.record_path,
            next_page_path=body.next_page_path,
            connection_name=body.connection_name,
            schedule_interval_minutes=body.schedule_interval_minutes,
            cursor_property=body.cursor_property,
            incremental_param=body.incremental_param,
            reserved_dataset_names=await _reserved_dataset_names(app.state.pool),
        )
    except generic_source_registry.SourceConflictError as exc:
        raise HolonError.conflict('PluginConflict', str(exc)) from exc
    except generic_source_registry.SourceConfigError as exc:
        raise HolonError.invalid_argument('PluginValidationFailed', str(exc)) from exc
    emit_audit(
        category="access",
        action="connectivity.source.registered",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="source",
        resource_urn=build_urn(principal.tenant_id, target_workspace, "source", body.name),
        extra={"base_url": body.base_url},
    )
    return registration


@app.get("/sources")
async def list_sources(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await generic_source_registry.list_sources(app.state.pool, principal.tenant_id)


def _source_not_found(name: str) -> HolonError:
    return HolonError.not_found("SourceNotFound", f"no source registered as {name!r}", name=name)


@app.post("/sources/{name}/disable")
async def disable_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    source = await generic_source_registry.get_source(app.state.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    return await generic_source_registry.set_source_status(app.state.pool, principal.tenant_id, name, "disabled")


@app.post("/sources/{name}/enable")
async def enable_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    source = await generic_source_registry.get_source(app.state.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    return await generic_source_registry.set_source_status(app.state.pool, principal.tenant_id, name, "active")


@app.delete("/sources/{name}")
async def delete_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    source = await generic_source_registry.get_source(app.state.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    await generic_source_registry.delete_source(app.state.pool, principal.tenant_id, name)
    emit_audit(
        category="access",
        action="connectivity.source.deleted",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="source",
        resource_urn=build_urn(principal.tenant_id, WORKSPACE_ID, "source", name),
    )
    return {"deleted": name}


# --- SQL sources (Postgres wire: PostgreSQL, Redshift, CockroachDB) --------


class RegisterSqlConnectionRequest(BaseModel):
    name: str
    host: str
    port: int = 5432
    database: str
    username: str
    # Optional; if omitted on edit, existing secret is retained
    password: Optional[str] = None
    secret_ref: Optional[str] = None


@app.post("/sql-connections")
async def register_sql_connection(
    body: RegisterSqlConnectionRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Register or update a SQL connection credential."""
    await _authorize_workspace(principal, "write")
    try:
        return await sql_source_registry.register_connection(
            app.state.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            host=body.host,
            port=body.port,
            database=body.database,
            username=body.username,
            password=body.password,
            secret_ref=body.secret_ref,
            created_by_urn=principal.urn,
        )
    except sql_source_registry.SourceConfigError as exc:
        raise HolonError.invalid_argument("SourceValidationFailed", str(exc)) from exc


@app.get("/sql-connections")
async def list_sql_connections(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await sql_source_registry.list_connections(app.state.pool, principal.tenant_id)


@app.delete("/sql-connections/{name}")
async def delete_sql_connection(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    if await sql_source_registry.get_connection(app.state.pool, principal.tenant_id, name) is None:
        raise HolonError.not_found('ConnectionNotFound', f"no SQL connection registered as {name!r}", name=name)
    try:
        await sql_source_registry.delete_connection(app.state.pool, principal.tenant_id, name)
    except sql_source_registry.ConnectionInUseError as exc:
        raise HolonError.conflict('ConnectionConflict', str(exc)) from exc
    return {"deleted": name}


class RegisterSqlSourceRequest(BaseModel):
    name: str
    connection_name: str
    workspace_id: Optional[str] = None
    table_name: Optional[str] = None
    query: Optional[str] = None
    schedule_interval_minutes: Optional[int] = None
    cursor_property: Optional[str] = None


@app.post("/sql-sources")
async def register_sql_source(
    body: RegisterSqlSourceRequest,
    principal: Principal = Depends(current_principal),
    workspace_id: Optional[str] = Query(None, alias="workspaceId"),
    x_holon_workspace_id: Optional[str] = Header(None, alias="X-Holon-Workspace-Id"),
) -> dict:
    """Register a new SQL database source."""
    target_workspace = _resolve_workspace(
        explicit=body.workspace_id,
        workspace_id=workspace_id,
        x_holon_workspace_id=x_holon_workspace_id,
    )
    await _authorize_workspace(principal, "write", workspace_id=target_workspace)
    try:
        registration = await sql_source_registry.register_source(
            app.state.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            workspace_id=target_workspace,
            connection_name=body.connection_name,
            table_name=body.table_name,
            query=body.query,
            schedule_interval_minutes=body.schedule_interval_minutes,
            cursor_property=body.cursor_property,
            created_by_urn=principal.urn,
            reserved_dataset_names=await _reserved_dataset_names(app.state.pool),
        )
    except sql_source_registry.SourceConflictError as exc:
        raise HolonError.conflict('SourceConflict', str(exc)) from exc
    except sql_source_registry.SourceConfigError as exc:
        raise HolonError.invalid_argument('SourceValidationFailed', str(exc)) from exc
    emit_audit(
        category="access",
        action="connectivity.sql_source.registered",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="source",
        resource_urn=build_urn(principal.tenant_id, target_workspace, "source", body.name),
        extra={"connection_name": body.connection_name},
    )
    return registration


@app.get("/sql-sources")
async def list_sql_sources(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await sql_source_registry.list_sources(app.state.pool, principal.tenant_id)


@app.post("/sql-sources/{name}/disable")
async def disable_sql_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    source = await sql_source_registry.get_source(app.state.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    return await sql_source_registry.set_source_status(app.state.pool, principal.tenant_id, name, "disabled")


@app.post("/sql-sources/{name}/enable")
async def enable_sql_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    source = await sql_source_registry.get_source(app.state.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    return await sql_source_registry.set_source_status(app.state.pool, principal.tenant_id, name, "active")


@app.delete("/sql-sources/{name}")
async def delete_sql_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    source = await sql_source_registry.get_source(app.state.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    await sql_source_registry.delete_source(app.state.pool, principal.tenant_id, name)
    emit_audit(
        category="access",
        action="connectivity.sql_source.deleted",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="source",
        resource_urn=build_urn(principal.tenant_id, WORKSPACE_ID, "source", name),
    )
    return {"deleted": name}


# --- Object storage sources (S3-compatible: S3, MinIO, GCS/Azure via S3 gateway) ---


class RegisterObjectConnectionRequest(BaseModel):
    name: str
    endpoint: str
    access_key_id: str
    region: str = "us-east-1"
    path_style: bool = True
    # Optional; if omitted on edit, existing secret is retained
    secret_access_key: Optional[str] = None
    secret_ref: Optional[str] = None


@app.post("/object-connections")
async def register_object_connection(
    body: RegisterObjectConnectionRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Register or update an object storage connection credential."""
    await _authorize_workspace(principal, "write")
    try:
        return await object_source_registry.register_connection(
            app.state.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            endpoint=body.endpoint,
            access_key_id=body.access_key_id,
            region=body.region,
            path_style=body.path_style,
            secret_access_key=body.secret_access_key,
            secret_ref=body.secret_ref,
            created_by_urn=principal.urn,
        )
    except object_source_registry.SourceConfigError as exc:
        raise HolonError.invalid_argument("SourceValidationFailed", str(exc)) from exc


@app.get("/object-connections")
async def list_object_connections(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await object_source_registry.list_connections(app.state.pool, principal.tenant_id)


@app.delete("/object-connections/{name}")
async def delete_object_connection(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    if await object_source_registry.get_connection(app.state.pool, principal.tenant_id, name) is None:
        raise HolonError.not_found('ConnectionNotFound', f"no object connection registered as {name!r}", name=name)
    try:
        await object_source_registry.delete_connection(app.state.pool, principal.tenant_id, name)
    except object_source_registry.ConnectionInUseError as exc:
        raise HolonError.conflict('ConnectionConflict', str(exc)) from exc
    return {"deleted": name}


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


@app.post("/object-sources")
async def register_object_source(
    body: RegisterObjectSourceRequest,
    principal: Principal = Depends(current_principal),
    workspace_id: Optional[str] = Query(None, alias="workspaceId"),
    x_holon_workspace_id: Optional[str] = Header(None, alias="X-Holon-Workspace-Id"),
) -> dict:
    """Register a new object storage source."""
    target_workspace = _resolve_workspace(
        explicit=body.workspace_id,
        workspace_id=workspace_id,
        x_holon_workspace_id=x_holon_workspace_id,
    )
    await _authorize_workspace(principal, "write", workspace_id=target_workspace)
    try:
        registration = await object_source_registry.register_source(
            app.state.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            workspace_id=target_workspace,
            connection_name=body.connection_name,
            bucket=body.bucket,
            format=body.format,
            object_key=body.object_key,
            key_prefix=body.key_prefix,
            incremental=body.incremental,
            schedule_interval_minutes=body.schedule_interval_minutes,
            created_by_urn=principal.urn,
            reserved_dataset_names=await _reserved_dataset_names(app.state.pool),
        )
    except object_source_registry.SourceConflictError as exc:
        raise HolonError.conflict('SourceConflict', str(exc)) from exc
    except object_source_registry.SourceConfigError as exc:
        raise HolonError.invalid_argument('SourceValidationFailed', str(exc)) from exc
    emit_audit(
        category="access",
        action="connectivity.object_source.registered",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="source",
        resource_urn=build_urn(principal.tenant_id, target_workspace, "source", body.name),
        extra={"connection_name": body.connection_name},
    )
    return registration


@app.get("/object-sources")
async def list_object_sources(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await object_source_registry.list_sources(app.state.pool, principal.tenant_id)


@app.post("/object-sources/{name}/disable")
async def disable_object_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    source = await object_source_registry.get_source(app.state.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    return await object_source_registry.set_source_status(app.state.pool, principal.tenant_id, name, "disabled")


@app.post("/object-sources/{name}/enable")
async def enable_object_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    source = await object_source_registry.get_source(app.state.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    return await object_source_registry.set_source_status(app.state.pool, principal.tenant_id, name, "active")


@app.delete("/object-sources/{name}")
async def delete_object_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    source = await object_source_registry.get_source(app.state.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    await object_source_registry.delete_source(app.state.pool, principal.tenant_id, name)
    emit_audit(
        category="access",
        action="connectivity.object_source.deleted",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="source",
        resource_urn=build_urn(principal.tenant_id, WORKSPACE_ID, "source", name),
    )
    return {"deleted": name}


# Writeback endpoint used by Automation Workflow Engine
CLOSE_ACCOUNT_FAILURE_SENTINEL = "__simulate_failure__"
WORKFLOW_ENGINE_LOCAL_NAME = "automation-workflow-engine"
WORKFLOW_ENGINE_URN = build_urn(TENANT_ID, "global", "service-account", WORKFLOW_ENGINE_LOCAL_NAME)


class CloseAccountRequest(BaseModel):
    reason: str


def _require_workflow_engine(principal: Principal) -> None:
    """Verify caller is the Automation Workflow Engine service account."""
    expected_suffix = f":global:service-account:{WORKFLOW_ENGINE_LOCAL_NAME}"
    if principal.type != "service_account" or not principal.urn.endswith(expected_suffix):
        raise HolonError.forbidden(
            "AutomationOnlyEndpoint",
            "close-account is restricted to Automation's Workflow Engine — use the approval flow",
        )


@app.post("/source/customers/{customer_id}/close-account")
async def close_source_customer_account(
    customer_id: int, request: CloseAccountRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """`reason == CLOSE_ACCOUNT_FAILURE_SENTINEL` is a documented test hook
    for exercising the saga's compensation path deterministically — not a
    real failure mode.
    """
    _require_workflow_engine(principal)
    await _authorize_workspace(principal, "write")
    if request.reason == CLOSE_ACCOUNT_FAILURE_SENTINEL:
        raise HolonError.internal('InternalError', "simulated downstream failure")

    conn = await asyncpg.connect(_source_db_url())
    try:
        row = await conn.fetchrow(
            "UPDATE customers SET account_closed = true WHERE id = $1 RETURNING id, account_closed",
            customer_id,
        )
    finally:
        await conn.close()

    if row is None:
        raise HolonError.not_found('SourceCustomerNotFound', f"customer {customer_id} not found in source_erp", customer_id=customer_id)
    return dict(row)


@app.get("/source/customers/{customer_id}")
async def get_source_customer(customer_id: int, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "read")
    conn = await asyncpg.connect(_source_db_url())
    try:
        row = await conn.fetchrow("SELECT id, account_closed FROM customers WHERE id = $1", customer_id)
    finally:
        await conn.close()

    if row is None:
        raise HolonError.not_found('SourceCustomerNotFound', f"customer {customer_id} not found in source_erp", customer_id=customer_id)
    return dict(row)


class RegisterWriteTargetRequest(BaseModel):
    dataset_name: str
    table_name: str
    id_column: str
    allowed_properties: dict[str, str]


@app.post("/write-targets", status_code=201)
async def register_write_target(
    request: RegisterWriteTargetRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Register a writeback target schema for declarative actions."""
    await _authorize_workspace(principal, "write")
    try:
        return await write_target_registry.register_write_target(
            app.state.pool,
            tenant_id=principal.tenant_id,
            dataset_name=request.dataset_name,
            table_name=request.table_name,
            id_column=request.id_column,
            allowed_properties=request.allowed_properties,
            created_by_urn=principal.urn,
        )
    except write_target_registry.WriteTargetConfigError as exc:
        raise HolonError.invalid_argument('PluginValidationFailed', str(exc)) from exc


@app.get("/write-targets")
async def list_write_targets(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await write_target_registry.list_write_targets(app.state.pool, principal.tenant_id)


@app.get("/write-targets/{dataset_name}")
async def get_write_target(dataset_name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "read")
    target = await write_target_registry.get_write_target(app.state.pool, principal.tenant_id, dataset_name)
    if target is None:
        raise HolonError.not_found('DatasetNotFound', f"no write target registered for dataset {dataset_name!r}")
    return target


@app.delete("/write-targets/{dataset_name}")
async def delete_write_target(dataset_name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    if await write_target_registry.get_write_target(app.state.pool, principal.tenant_id, dataset_name) is None:
        raise HolonError.not_found('DatasetNotFound', f"no write target registered for dataset {dataset_name!r}")
    await write_target_registry.delete_write_target(app.state.pool, principal.tenant_id, dataset_name)
    emit_audit(
        category="access",
        action="connectivity.write_target.deleted",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="write_target",
        resource_urn=build_urn(principal.tenant_id, "global", "write-target", dataset_name),
    )
    return {"deleted": dataset_name}


class WriteSourceRequest(BaseModel):
    edits: dict[str, object]


@app.post("/source/{dataset_name}/{instance_id}/write")
async def write_source(
    dataset_name: str, instance_id: str, request: WriteSourceRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Apply writeback edits to a target dataset instance."""
    _require_workflow_engine(principal)
    await _authorize_workspace(principal, "write")
    try:
        return await write_target_registry.apply_write(
            app.state.pool, _source_db_url(),
            tenant_id=principal.tenant_id, dataset_name=dataset_name, instance_id=instance_id, edits=request.edits,
        )
    except write_target_registry.UnknownWriteTargetError as exc:
        raise HolonError.not_found("WriteTargetNotFound", str(exc)) from exc
    except write_target_registry.InstanceNotFoundError as exc:
        raise HolonError.not_found("WriteTargetInstanceNotFound", str(exc)) from exc
    except write_target_registry.WriteTargetConfigError as exc:
        raise HolonError.invalid_argument('SourceValidationFailed', str(exc)) from exc
