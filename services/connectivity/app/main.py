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
    run_migrations,
)
from holon_common.audit import clear_durable_audit_hooks, emit_audit
from holon_common.audit_store import (
    ensure_schema as ensure_audit_schema,
    install_durable_audit,
    list_events_page,
)
from holon_common.authz import PermissionClient

from . import (
    generic_source_registry,
    iceberg_reader,
    iceberg_writer,
    pipeline,
    plugin_registry,
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

CONNECTOR_URN_STREAM = build_urn(TENANT_ID, "global", "connector", "inventory-kafka-stream")
CONNECTOR_URN_PIPELINE = build_urn(TENANT_ID, "global", "connector", "pipeline-transform")

STREAM_INGEST_URN = build_urn(TENANT_ID, "global", "service-account", "connectivity-stream-ingest")
SCHEDULER_ACTOR_URN = build_urn(TENANT_ID, "global", "service-account", "connectivity-scheduler")
SCHEDULER_POLL_SECONDS = 60

# Stream connector owns this dataset name; plugins/sources cannot claim it.
RESERVED_DATASET_NAMES = frozenset({"inventory_levels"})

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

    # Opt-in continuous ingest (not demo seed). Default off — operators
    # register batch plugins/sources; enable when inventory Kafka is wired.
    stream_task: Optional[asyncio.Task] = None
    if os.environ.get("HOLON_ENABLE_INVENTORY_STREAM", "").lower() in {"1", "true", "yes"}:
        stream_task = asyncio.create_task(
            stream_connector.consume_inventory_stream_forever(
                kafka_bootstrap=KAFKA_BOOTSTRAP,
                iceberg_config=ICEBERG_CONFIG,
                connector_urn=CONNECTOR_URN_STREAM,
                pool=app.state.pool,
                record_sync=functools.partial(
                    _finalize_sync,
                    actor=EventActor(type="service_account", urn=STREAM_INGEST_URN, on_behalf_of=None),
                ),
            )
        )

    scheduler_task = asyncio.create_task(run_scheduler_forever(app.state.pool))

    yield

    scheduler_task.cancel()
    if stream_task is not None:
        stream_task.cancel()
    relay_task.cancel()
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
    """Workspace ReBAC gate — same container check Knowledge/Experience use."""
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
    await app.state.pool.fetchval("SELECT 1")
    return {"status": "ok", "quiesced": await _is_quiesced(app.state.pool)}


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
    """Freeze scheduled ingestion for a consistent snapshot (operator
    backup). Shared across all Connectivity replicas via Postgres.
    Does not stop in-flight HTTP `/sync` — deployer should drain
    those separately. See docs/ops/backup-restore.md.
    """
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
    """Shared by `run_sync`, `stream_connector`'s background task, and
    each `POST /pipelines/{name}/run` TransformStep — event
    construction, `sync_run` bookkeeping, and outbox enqueue don't care
    which triggered the sync, only what it produced.

    `tenant_id`/`workspace_id` default to the bootstrap env values for
    demo connectors and background tasks; HTTP callers that sync a
    per-filiale generic source pass `principal.tenant_id` (ADR 026).
    """
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
    """The actual sync — dispatch, fetch, write, finalize. Factored out
    of the `/sync` route so the scheduler background task (below) can
    trigger the identical path under its own service-account actor,
    instead of duplicating this dispatch a second time. Raises
    `HolonError` on a bad dataset/fetch failure even though the
    scheduler is not a request handler — it's just a plain exception
    carrying a status code and message there, caught and logged like any
    other error, never turned into an actual HTTP response.

    Runtime dispatch: active plugin for this tenant → generic REST source.
    No privileged in-process dataset map.
    """
    write_mode: Literal["overwrite", "append"] = "overwrite"
    plugin = await plugin_registry.load_active_plugin_for_dataset(app.state.pool, dataset_name, tenant_id)
    if plugin is not None:
        local_name = plugin.manifest.connector_local_name or f"plugin-{plugin.manifest.name}"
        connector_urn = build_urn(tenant_id, "global", "connector", local_name)
        read = plugin.fetch
    else:
        source = await generic_source_registry.get_source(app.state.pool, tenant_id, dataset_name)
        if source is None:
            raise HolonError.not_found('DatasetNotFound', f"unknown dataset: {dataset_name}", dataset_name=dataset_name)
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

    started_at = datetime.now(timezone.utc)
    try:
        rows = await read()
    except generic_source_registry.SourceFetchError as exc:
        raise HolonError.invalid_argument('DatasetValidationFailed', str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HolonError.invalid_argument('SourceHttpError', f"source returned {exc.response.status_code}: {exc.response.text[:300]}") from exc
    except httpx.RequestError as exc:
        raise HolonError.invalid_argument('SourceUnreachable', f"could not reach the source: {exc}") from exc
    result = await asyncio.to_thread(iceberg_writer.write_snapshot, rows, dataset_name, mode=write_mode, **ICEBERG_CONFIG)
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
    """The Kestra idea (decouple "when" from "how"), built native: this
    loop only ever decides *whether* a source is due — `_run_sync_for_dataset`,
    the exact same path `/sync` uses, does the actual work, so a
    scheduled sync is indistinguishable downstream from a manual one.

    Multi-replica safe: each poll tries `pg_try_advisory_lock` so only one
    Connectivity pod runs due-source work per tick. Quiesce is read from
    `connectivity_runtime` (shared), not process memory.
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
        # pipeline_run, not sync_run: a pipeline's own run log, not the
        # dataset-sync log its steps' outputs also land in — a multi-step
        # pipeline has several output datasets, none of which is "the
        # pipeline" itself. Only a *succeeded* run counts, same as
        # sources/plugins (sync_run never records a failed attempt at
        # all) — a failed run must not push the next retry a full
        # interval out; it should be picked up on the very next poll.
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
                    # Same due-check, for connector plugins — plugins have
                    # no workspace_id of their own (see plugin_registration's
                    # schema), so scheduled plugin syncs always land in the
                    # default workspace, same as an unscoped manual /sync.
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
    """Mints a short-lived service-account token directly (same trust
    level already extended to every service holding `HOLON_JWT_SECRET`,
    e.g. Knowledge's own `_identity_validation_token` for its Identity
    calls) rather than round-tripping through Identity's `/token` — an
    internal service-to-service call to Knowledge's
    `POST /functions/{name}/invoke`, not a client-facing sign-in.
    """
    principal = Principal(
        urn=PIPELINE_FUNCTION_CALLER_URN, type="service_account", tenant_id=TENANT_ID,
        display_name="Connectivity Pipeline Runner",
    )
    return issue_token(
        principal, JWT_SECRET, ttl_seconds=60, kid=JWT_ACTIVE_KID, secrets=JWT_SECRETS
    )


async def _latest_dataset_version_urn(dataset_name: str) -> Optional[str]:
    """Connectivity's own `sync_run` bookkeeping already records every
    dataset this platform has ever produced — registered plugin,
    generic source, or an earlier pipeline step's own output, since
    `_finalize_sync` writes one row regardless of source. No cross-service
    call to Knowledge's catalog needed: this platform is the one thing
    that's authoritative on "what did I just produce."
    """
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
    # Foundry Pipeline Builder "logical type cast" — column → Value Type name.
    # Validated via Knowledge POST /value-types/validate-casts before write.
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
    """Drives a `PipelineDefinition`'s steps strictly in the order
    declared (see `pipeline.py`'s module docstring for the DAG-shape
    scope note): read the input Iceberg table, invoke the named Function
    over every row via Knowledge's `POST /functions/{name}/invoke`, write
    the result as a new snapshot, then finalize through the *same*
    `_finalize_sync` every connector's `/sync` already uses — so
    Catalog picks each step's output up through the existing,
    unmodified `connectivity.sync.completed` consumer, with real
    dataset -> dataset lineage via `source_dataset_version_urn`.

    Factored out of the `/run` route so the scheduler (below) can
    trigger the identical path, same as `_run_sync_for_dataset`.
    """
    definition = await pipeline.get_pipeline(app.state.pool, name)
    if definition is None:
        raise HolonError.not_found('PipelineNotFound', f"unknown pipeline: {name}", name=name)

    run_started_at = datetime.now(timezone.utc)
    step_results: list[dict] = []

    try:
        for step in definition["steps"]:
            step_started_at = datetime.now(timezone.utc)
            source_dataset_version_urn = await _latest_dataset_version_urn(step["input_dataset"])

            input_rows = await asyncio.to_thread(iceberg_reader.read_table, step["input_dataset"], **ICEBERG_CONFIG)

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
                iceberg_writer.write_snapshot, output_rows, step["output_dataset"], **ICEBERG_CONFIG
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
    """Same scheduling model as `/sources` and `/plugins`."""
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


class RegisterPluginRequest(BaseModel):
    entry_point: str


@app.get("/plugins")
async def list_plugins(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await plugin_registry.list_plugin_registrations(app.state.pool, principal.tenant_id)


@app.post("/plugins")
async def register_plugin(body: RegisterPluginRequest, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    """Registers (or re-registers) a Connector plugin by its dynamically-importable
    entry point. See `plugin_registry.py`'s module docstring for details.
    """
    try:
        registration = await plugin_registry.register_plugin(
            app.state.pool,
            entry_point=body.entry_point,
            tenant_id=principal.tenant_id,
            reserved_dataset_names=RESERVED_DATASET_NAMES,
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
    registration = await plugin_registry.get_plugin_registration(app.state.pool, name)
    if registration is None:
        raise _plugin_not_found(name)
    return registration


@app.post("/plugins/{name}/disable")
async def disable_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    """Deactivatable without redeploy: `run_sync`'s fallback path
    checks `status = 'active'` on every call, so this takes effect on the
    very next sync attempt, not after a restart.
    """
    registration = await plugin_registry.get_plugin_registration(app.state.pool, name)
    if registration is None:
        raise _plugin_not_found(name)
    result = await plugin_registry.set_plugin_status(app.state.pool, name, "disabled")
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
    registration = await plugin_registry.get_plugin_registration(app.state.pool, name)
    if registration is None:
        raise _plugin_not_found(name)
    result = await plugin_registry.set_plugin_status(app.state.pool, name, "active")
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
    """Same scheduling model as `/sources` — `run_scheduler_forever` picks
    this up on its next poll, no restart needed. `null` clears it back to
    manual-only, same as omitting `schedule_interval_minutes` on a source.
    """
    await _authorize_workspace(principal, "write")
    if body.schedule_interval_minutes is not None and body.schedule_interval_minutes <= 0:
        raise HolonError.invalid_argument(
            "InvalidSchedule", "schedule_interval_minutes must be a positive number of minutes"
        )
    registration = await plugin_registry.get_plugin_registration(app.state.pool, name)
    if registration is None:
        raise _plugin_not_found(name)
    result = await plugin_registry.set_plugin_schedule(app.state.pool, name, body.schedule_interval_minutes)
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
    auth_header_name: str
    # Optional; if omitted on edit, existing secret is retained
    auth_header_value: Optional[str] = None


@app.post("/connections")
async def register_connection(body: RegisterConnectionRequest, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    """Reusable auth for the no-code connector: configure a credential
    once, point as many sources at it as needed, instead of re-pasting
    the same secret into every one. Same authentication tier as
    `/sources`/`/plugins`. A real upsert (same `name` again updates it)
    — this is also how editing a connection works, no separate PUT route.
    """
    return await generic_source_registry.register_connection(
        app.state.pool,
        tenant_id=principal.tenant_id,
        name=body.name,
        auth_header_name=body.auth_header_name,
        auth_header_value=body.auth_header_value,
        created_by_urn=principal.urn,
    )


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
    """The no-code connector: register a REST API by URL alone, no Python
    to write or deploy. Same authentication tier as `/plugins` (any
    authenticated principal) — registering a data source is exactly the
    same class of action as registering a plugin, just without the code.
    """
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
            reserved_dataset_names=RESERVED_DATASET_NAMES,
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
    """Pauses without losing configuration: `run_sync`'s dispatch only
    matches an `active` row, so this takes effect on the very next sync
    attempt, same as `/plugins/{name}/disable`.
    """
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


# Writeback endpoint used by Automation Workflow Engine
CLOSE_ACCOUNT_FAILURE_SENTINEL = "__simulate_failure__"
WORKFLOW_ENGINE_LOCAL_NAME = "automation-workflow-engine"
WORKFLOW_ENGINE_URN = build_urn(TENANT_ID, "global", "service-account", WORKFLOW_ENGINE_LOCAL_NAME)


class CloseAccountRequest(BaseModel):
    reason: str


def _require_workflow_engine(principal: Principal) -> None:
    """Accept the Automation Workflow Engine for any tenant (URN local name
    is fixed; tenant segment follows the action's tenant_id).
    """
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
    """Declares which table/columns a declarative Action's writeback may
    target — auth-only, same tier as `POST /sources`/`POST /pipelines`
    (this service's existing, uniform security model; the actually
    sensitive operation, the write itself, is separately gated to
    Automation's Workflow Engine by `POST /source/{dataset_name}
    /{instance_id}/write` below).
    """
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
    """The generic counterpart to `close_source_customer_account` above —
    any declarative Action Type with a `writeback_dataset` goes through
    this one endpoint instead of getting its own bespoke route. Same
    restriction: only Automation's Workflow Engine may call it, never a
    connector's own sync path or a direct client call.
    """
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
