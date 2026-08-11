"""Connectivity Platform — Connector execution and ingestion.

Runs core connectors (PostgreSQL, MongoDB, generic REST, CSV file import,
continuous Kafka stream), lands each in the Iceberg raw zone, and announces
every new snapshot on the Platform Event Bus via a transactional outbox.
Everything downstream — cataloguing, ontology mapping, dashboards —
reacts to that event.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg
import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from holon_common import (
    EventActor,
    EventEnvelope,
    EventProducer,
    Principal,
    active_jwt,
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
)

from . import (
    connector,
    file_connector,
    generic_source_registry,
    iceberg_reader,
    iceberg_writer,
    mongo_connector,
    pipeline,
    plugin_registry,
    rest_connector,
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
SOURCE_DB_URL = os.environ["HOLON_SOURCE_DB_URL"]
MONGO_URL = os.environ["HOLON_MONGO_URL"]
REVIEWS_API_URL = os.environ["HOLON_REVIEWS_API_URL"]
KNOWLEDGE_URL = os.environ["HOLON_KNOWLEDGE_URL"]
KAFKA_BOOTSTRAP = os.environ["HOLON_KAFKA_BOOTSTRAP"]
OTLP_ENDPOINT = os.environ["HOLON_OTLP_ENDPOINT"]

ICEBERG_CONFIG = dict(
    catalog_uri=os.environ["HOLON_ICEBERG_CATALOG_URI"],
    warehouse=os.environ["HOLON_ICEBERG_WAREHOUSE"],
    s3_endpoint=os.environ["HOLON_S3_ENDPOINT"],
    access_key=os.environ["AWS_ACCESS_KEY_ID"],
    secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    region=os.environ["AWS_REGION"],
)

# Distinct connectors registered against this platform —
# one per source system, not one per dataset.
CONNECTOR_URN_POSTGRES = build_urn(TENANT_ID, "global", "connector", "postgres-source-erp")
CONNECTOR_URN_MONGO = build_urn(TENANT_ID, "global", "connector", "mongodb-support-desk")
CONNECTOR_URN_REST = build_urn(TENANT_ID, "global", "connector", "reviews-rest-api")
CONNECTOR_URN_FILE = build_urn(TENANT_ID, "global", "connector", "csv-landing-suppliers")
CONNECTOR_URN_STREAM = build_urn(TENANT_ID, "global", "connector", "inventory-kafka-stream")

# every pipeline run's outputs are attributed to this one
# synthetic connector — a pipeline transforms already-catalogued data,
# it isn't itself a new external source system the way the five above are.
CONNECTOR_URN_PIPELINE = build_urn(TENANT_ID, "global", "connector", "pipeline-transform")

# The streaming connector has no `Principal` to attribute events to —
# nothing called an API to trigger it — so it acts under its own
# service-account identity, same pattern as Knowledge's
# `knowledge-saga-orchestrator` (`actions.py`).
STREAM_INGEST_URN = build_urn(TENANT_ID, "global", "service-account", "connectivity-stream-ingest")

# Same reasoning as `STREAM_INGEST_URN` — the scheduler loop below has no
# `Principal` of its own (nothing called an API to trigger a scheduled
# sync), so it acts as its own service-account identity.
SCHEDULER_ACTOR_URN = build_urn(TENANT_ID, "global", "service-account", "connectivity-scheduler")
SCHEDULER_POLL_SECONDS = 60

# Every dataset this platform can sync, paired with the connector that owns
# it and a zero-arg async reader — `run_sync` doesn't need to know which
# driver or connection string each source actually uses.
DATASET_READERS = {
    "customers": {
        "connector_urn": CONNECTOR_URN_POSTGRES,
        "read": lambda: connector.read_customers(SOURCE_DB_URL),
    },
    "orders": {
        "connector_urn": CONNECTOR_URN_POSTGRES,
        "read": lambda: connector.read_orders(SOURCE_DB_URL),
    },
    "support_tickets": {
        "connector_urn": CONNECTOR_URN_MONGO,
        "read": lambda: asyncio.to_thread(mongo_connector.read_support_tickets, MONGO_URL),
    },
    "reviews": {
        "connector_urn": CONNECTOR_URN_REST,
        "read": lambda: rest_connector.read_reviews(REVIEWS_API_URL),
    },
    "suppliers": {
        "connector_urn": CONNECTOR_URN_FILE,
        "read": lambda: asyncio.to_thread(
            file_connector.read_suppliers_csv,
            s3_endpoint=ICEBERG_CONFIG["s3_endpoint"],
            access_key=ICEBERG_CONFIG["access_key"],
            secret_key=ICEBERG_CONFIG["secret_key"],
            region=ICEBERG_CONFIG["region"],
        ),
    },
}

# Every dataset a core connector owns. A plugin can never register itself
# against one of these.
CORE_DATASET_NAMES = frozenset(DATASET_READERS) | {"inventory_levels"}

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
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool(DB_URL)
    app.state.quiesced = False
    async with app.state.pool.acquire() as conn:
        await conn.execute(_DDL)
        await outbox.ensure_schema(conn)
        await plugin_registry.ensure_schema(conn)
        await generic_source_registry.ensure_schema(conn)
        await pipeline.ensure_schema(conn)
        await write_target_registry.ensure_schema(conn)

    app.state.producer = EventProducer(KAFKA_BOOTSTRAP)
    await app.state.producer.start()
    relay_task = asyncio.create_task(outbox.relay_forever(app.state.pool, app.state.producer, dlq_producer=app.state.producer))

    stream_task = asyncio.create_task(
        stream_connector.consume_inventory_stream_forever(
            kafka_bootstrap=KAFKA_BOOTSTRAP,
            iceberg_config=ICEBERG_CONFIG,
            connector_urn=CONNECTOR_URN_STREAM,
            record_sync=functools.partial(
                _finalize_sync,
                actor=EventActor(type="service_account", urn=STREAM_INGEST_URN, on_behalf_of=None),
            ),
        )
    )

    scheduler_task = asyncio.create_task(run_scheduler_forever(app.state.pool))

    yield

    scheduler_task.cancel()
    stream_task.cancel()
    relay_task.cancel()
    await app.state.producer.stop()
    await app.state.pool.close()


app = FastAPI(title="Holon — Connectivity Platform", lifespan=lifespan)
instrument_cors(app)  # Sources page (Experience) calls this service directly from the browser now
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
install_error_handlers(app, service_name=SERVICE_NAME)
current_principal = make_principal_dependency(JWT_SECRET, secrets=JWT_SECRETS)


class SyncRequest(BaseModel):
    dataset: str = "customers"


class SyncResult(BaseModel):
    dataset_urn: str
    dataset_version_urn: str
    snapshot_id: int
    row_count: int
    location: str


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/live")
async def live() -> dict:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict:
    await app.state.pool.fetchval("SELECT 1")
    return {"status": "ok", "quiesced": bool(getattr(app.state, "quiesced", False))}


class QuiesceRequest(BaseModel):
    quiesced: bool = True


@app.post("/admin/quiesce")
async def admin_quiesce(body: QuiesceRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Freeze scheduled ingestion for a consistent snapshot (operator
    backup). Does not stop in-flight HTTP `/sync` — deployer should drain
    those separately. See docs/ops/backup-restore.md.
    """
    require_tenant_match(principal, TENANT_ID)  # bootstrap admins only for instance quiesce
    app.state.quiesced = body.quiesced
    return {"quiesced": app.state.quiesced}


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
    `HTTPException` on a bad dataset/fetch failure even though the
    scheduler is not a request handler — it's just a plain exception
    carrying a status code and message there, caught and logged like any
    other error, never turned into an actual HTTP response.

    Built-in `DATASET_READERS` connectors are bootstrap-tenant demo
    fixtures (module-level URNs). Per-filiale work uses generic REST /
    plugins keyed by `tenant_id` (ADR 026).
    """
    spec = DATASET_READERS.get(dataset_name)
    if spec is not None:
        if tenant_id != TENANT_ID:
            raise HTTPException(
                status_code=403,
                detail="built-in demo connectors are scoped to the bootstrap tenant; "
                "provision per-filiale sources via /sources",
            )
        connector_urn = spec["connector_urn"]
        read = spec["read"]
    else:
        plugin = await plugin_registry.load_active_plugin_for_dataset(app.state.pool, dataset_name)
        if plugin is not None:
            connector_urn = build_urn(tenant_id, "global", "connector", f"plugin-{plugin.manifest.name}")
            read = plugin.fetch
        elif await generic_source_registry.is_registered(app.state.pool, tenant_id, dataset_name):
            connector_urn = build_urn(tenant_id, "global", "connector", f"generic-rest-{dataset_name}")
            read = functools.partial(generic_source_registry.fetch_for_dataset, app.state.pool, tenant_id, dataset_name)
        else:
            source = await generic_source_registry.get_source(app.state.pool, tenant_id, dataset_name)
            if source is not None:
                raise HTTPException(status_code=409, detail=f"source {dataset_name!r} is disabled — enable it first")
            raise HTTPException(status_code=404, detail=f"unknown dataset: {dataset_name}")

    started_at = datetime.now(timezone.utc)
    try:
        rows = await read()
    except generic_source_registry.SourceFetchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=400, detail=f"source returned {exc.response.status_code}: {exc.response.text[:300]}"
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=400, detail=f"could not reach the source: {exc}") from exc
    result = await asyncio.to_thread(iceberg_writer.write_snapshot, rows, dataset_name, **ICEBERG_CONFIG)
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
async def run_sync(request: SyncRequest = SyncRequest(), principal: Principal = Depends(current_principal)) -> SyncResult:
    if request.dataset in DATASET_READERS:
        require_tenant_match(principal, TENANT_ID)
    actor = EventActor(type=principal.type, urn=principal.urn, on_behalf_of=principal.on_behalf_of)
    return await _run_sync_for_dataset(
        request.dataset, actor=actor, tenant_id=principal.tenant_id, workspace_id=WORKSPACE_ID
    )


async def run_scheduler_forever(pool: asyncpg.Pool) -> None:
    """The Kestra idea (decouple "when" from "how"), built native: this
    loop only ever decides *whether* a source is due — `_run_sync_for_dataset`,
    the exact same path `/sync` uses, does the actual work, so a
    scheduled sync is indistinguishable downstream from a manual one.

    "Due" is computed against `sync_run.finished_at` (already recorded by
    every sync regardless of trigger), not a second last-run column on
    the source itself — one source of truth for "when did this last
    actually sync", never two that could drift apart.

    One source failing (bad URL, source now down) is logged and skipped,
    never allowed to stop the loop or block any other source's turn —
    the same resilience `catalog.consume_events` already applies to a
    poison event.
    """
    actor = EventActor(type="service_account", urn=SCHEDULER_ACTOR_URN, on_behalf_of=None)
    while True:
        try:
            if getattr(app.state, "quiesced", False):
                await asyncio.sleep(SCHEDULER_POLL_SECONDS)
                continue
            sources = await generic_source_registry.list_all_scheduled_sources(pool)
            for source in sources:
                dataset_name = source["name"]
                tenant_id = source["tenant_id"]
                interval = timedelta(minutes=source["schedule_interval_minutes"])
                dataset_urn = build_urn(tenant_id, WORKSPACE_ID, "dataset", dataset_name)
                last_finished_at = await pool.fetchval(
                    "SELECT finished_at FROM sync_run WHERE tenant_id = $1 AND dataset_urn = $2 "
                    "ORDER BY finished_at DESC LIMIT 1",
                    tenant_id, dataset_urn,
                )
                due = last_finished_at is None or (datetime.now(timezone.utc) - last_finished_at) >= interval
                if not due:
                    continue
                try:
                    result = await _run_sync_for_dataset(
                        dataset_name, actor=actor, tenant_id=tenant_id, workspace_id=WORKSPACE_ID
                    )
                    logger.info(
                        "scheduled sync completed for %r (tenant=%s): %d rows",
                        dataset_name,
                        tenant_id,
                        result.row_count,
                    )
                except Exception:
                    logger.exception(
                        "scheduled sync failed for %r (tenant=%s) — will retry next poll",
                        dataset_name,
                        tenant_id,
                    )
        except Exception:
            logger.exception("scheduler loop iteration failed — will retry")
        await asyncio.sleep(SCHEDULER_POLL_SECONDS)


@app.get("/syncs")
async def list_syncs(principal: Principal = Depends(current_principal)) -> list[dict]:
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
    dataset this platform has ever produced — core connector, registered
    plugin, or an earlier pipeline step's own output, since
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


class CreatePipelineRequest(BaseModel):
    steps: list[TransformStep]


@app.post("/pipelines/{name}", status_code=201)
async def create_pipeline(
    name: str, request: CreatePipelineRequest, principal: Principal = Depends(current_principal)
) -> dict:
    try:
        return await pipeline.create_pipeline(
            app.state.pool,
            tenant_id=principal.tenant_id,
            name=name,
            steps=[step.model_dump() for step in request.steps],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/pipelines")
async def list_pipelines(principal: Principal = Depends(current_principal)) -> list[dict]:
    return await pipeline.list_pipelines(app.state.pool, principal.tenant_id)


@app.get("/pipelines/{name}")
async def get_pipeline(name: str, principal: Principal = Depends(current_principal)) -> dict:
    definition = await pipeline.get_pipeline(app.state.pool, name)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"unknown pipeline: {name}")
    return definition


@app.get("/pipelines/{name}/runs")
async def list_pipeline_runs(name: str, principal: Principal = Depends(current_principal)) -> list[dict]:
    return await pipeline.list_runs(app.state.pool, principal.tenant_id, name)


@app.post("/pipelines/{name}/run")
async def run_pipeline(name: str, principal: Principal = Depends(current_principal)) -> dict:
    """Drives a `PipelineDefinition`'s steps strictly in the order
    declared (see `pipeline.py`'s module docstring for the DAG-shape
    scope note): read the input Iceberg table, invoke the named Function
    over every row via Knowledge's `POST /functions/{name}/invoke`, write
    the result as a new snapshot, then finalize through the *same*
    `_finalize_sync` every core connector's `/sync` already uses — so
    Catalog picks each step's output up through the existing,
    unmodified `connectivity.sync.completed` consumer, with real
    dataset -> dataset lineage via `source_dataset_version_urn`.
    """
    definition = await pipeline.get_pipeline(app.state.pool, name)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"unknown pipeline: {name}")

    run_started_at = datetime.now(timezone.utc)
    step_results: list[dict] = []
    actor = EventActor(type=principal.type, urn=principal.urn, on_behalf_of=principal.on_behalf_of)

    try:
        for step in definition["steps"]:
            step_started_at = datetime.now(timezone.utc)
            source_dataset_version_urn = await _latest_dataset_version_urn(step["input_dataset"])

            input_rows = await asyncio.to_thread(iceberg_reader.read_table, step["input_dataset"], **ICEBERG_CONFIG)

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{KNOWLEDGE_URL}/functions/{step['function_name']}/invoke",
                    json={"rows": input_rows},
                    headers={"Authorization": f"Bearer {_function_invocation_token()}"},
                )
            response.raise_for_status()
            output_rows = response.json()["rows"]

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
            tenant_id=principal.tenant_id,
            pipeline_name=name,
            status="failed",
            started_at=run_started_at,
            finished_at=run_finished_at,
            step_results=step_results,
            error=str(exc),
        )
        raise HTTPException(status_code=400, detail=f"pipeline run failed: {exc}") from exc

    run_finished_at = datetime.now(timezone.utc)
    return await pipeline.record_run(
        app.state.pool,
        tenant_id=principal.tenant_id,
        pipeline_name=name,
        status="succeeded",
        started_at=run_started_at,
        finished_at=run_finished_at,
        step_results=step_results,
    )


class RegisterPluginRequest(BaseModel):
    entry_point: str


@app.post("/plugins")
async def register_plugin(body: RegisterPluginRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Registers (or re-registers) a Connector plugin by its dynamically-importable
    entry point. See `plugin_registry.py`'s module docstring for details.
    """
    try:
        return await plugin_registry.register_plugin(
            app.state.pool, entry_point=body.entry_point, core_dataset_names=CORE_DATASET_NAMES
        )
    except plugin_registry.PluginConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _plugin_not_found(name: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"no plugin registered as {name!r}")


@app.get("/plugins/{name}")
async def get_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    registration = await plugin_registry.get_plugin_registration(app.state.pool, name)
    if registration is None:
        raise _plugin_not_found(name)
    return registration


@app.post("/plugins/{name}/disable")
async def disable_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    """Deactivatable without redeploy: `run_sync`'s fallback path
    checks `status = 'active'` on every call, so this takes effect on the
    very next sync attempt, not after a restart.
    """
    registration = await plugin_registry.get_plugin_registration(app.state.pool, name)
    if registration is None:
        raise _plugin_not_found(name)
    return await plugin_registry.set_plugin_status(app.state.pool, name, "disabled")


@app.post("/plugins/{name}/enable")
async def enable_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    registration = await plugin_registry.get_plugin_registration(app.state.pool, name)
    if registration is None:
        raise _plugin_not_found(name)
    return await plugin_registry.set_plugin_status(app.state.pool, name, "active")


class RegisterConnectionRequest(BaseModel):
    name: str
    auth_header_name: str
    # Optional so an edit (same `name`, upsert) can omit it to keep the
    # existing secret — same "never echoed back, COALESCE on update"
    # convention `RegisterSourceRequest.auth_header_value` already uses.
    # A brand-new connection still requires one; enforced client-side
    # the same way the source-edit form already does, not here.
    auth_header_value: Optional[str] = None


@app.post("/connections")
async def register_connection(body: RegisterConnectionRequest, principal: Principal = Depends(current_principal)) -> dict:
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
    return await generic_source_registry.list_connections(app.state.pool, principal.tenant_id)


@app.delete("/connections/{name}")
async def delete_connection(name: str, principal: Principal = Depends(current_principal)) -> dict:
    if await generic_source_registry.get_connection(app.state.pool, principal.tenant_id, name) is None:
        raise HTTPException(status_code=404, detail=f"no connection registered as {name!r}")
    try:
        await generic_source_registry.delete_connection(app.state.pool, principal.tenant_id, name)
    except generic_source_registry.ConnectionInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"deleted": name}


class RegisterSourceRequest(BaseModel):
    name: str
    base_url: str
    auth_header_name: Optional[str] = None
    auth_header_value: Optional[str] = None
    record_path: Optional[str] = None
    next_page_path: Optional[str] = None
    connection_name: Optional[str] = None
    schedule_interval_minutes: Optional[int] = None
    cursor_property: Optional[str] = None
    incremental_param: Optional[str] = None


@app.post("/sources")
async def register_source(body: RegisterSourceRequest, principal: Principal = Depends(current_principal)) -> dict:
    """The no-code connector: register a REST API by URL alone, no Python
    to write or deploy. Same authentication tier as `/plugins` (any
    authenticated principal) — registering a data source is exactly the
    same class of action as registering a plugin, just without the code.
    """
    try:
        return await generic_source_registry.register_source(
            app.state.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            base_url=body.base_url,
            created_by_urn=principal.urn,
            auth_header_name=body.auth_header_name,
            auth_header_value=body.auth_header_value,
            record_path=body.record_path,
            next_page_path=body.next_page_path,
            connection_name=body.connection_name,
            schedule_interval_minutes=body.schedule_interval_minutes,
            cursor_property=body.cursor_property,
            incremental_param=body.incremental_param,
            core_dataset_names=CORE_DATASET_NAMES,
        )
    except generic_source_registry.SourceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except generic_source_registry.SourceConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/sources")
async def list_sources(principal: Principal = Depends(current_principal)) -> list[dict]:
    return await generic_source_registry.list_sources(app.state.pool, principal.tenant_id)


def _source_not_found(name: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"no source registered as {name!r}")


@app.post("/sources/{name}/disable")
async def disable_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
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
    source = await generic_source_registry.get_source(app.state.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    return await generic_source_registry.set_source_status(app.state.pool, principal.tenant_id, name, "active")


@app.delete("/sources/{name}")
async def delete_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    source = await generic_source_registry.get_source(app.state.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    await generic_source_registry.delete_source(app.state.pool, principal.tenant_id, name)
    return {"deleted": name}


# One exception: a connector never writes back to its source, EXCEPT
# as part of an already-approved, audited ontology Action. This endpoint
# exists only to be called by Automation's Workflow Engine — the
# saga orchestrator for `Customer.closeAccount` — never by a connector's
# own sync path.
#
# `current_principal` alone proves a valid tenant-scoped JWT, which any
# authenticated principal has — it does NOT prove the caller is the workflow engine.
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
        raise HTTPException(
            status_code=403,
            detail="close-account is restricted to Automation's Workflow Engine — use the approval flow",
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
    if request.reason == CLOSE_ACCOUNT_FAILURE_SENTINEL:
        raise HTTPException(status_code=500, detail="simulated downstream failure")

    conn = await asyncpg.connect(SOURCE_DB_URL)
    try:
        row = await conn.fetchrow(
            "UPDATE customers SET account_closed = true WHERE id = $1 RETURNING id, account_closed",
            customer_id,
        )
    finally:
        await conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail=f"customer {customer_id} not found in source_erp")
    return dict(row)


@app.get("/source/customers/{customer_id}")
async def get_source_customer(customer_id: int, principal: Principal = Depends(current_principal)) -> dict:
    conn = await asyncpg.connect(SOURCE_DB_URL)
    try:
        row = await conn.fetchrow("SELECT id, account_closed FROM customers WHERE id = $1", customer_id)
    finally:
        await conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail=f"customer {customer_id} not found in source_erp")
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/write-targets")
async def list_write_targets(principal: Principal = Depends(current_principal)) -> list[dict]:
    return await write_target_registry.list_write_targets(app.state.pool, principal.tenant_id)


@app.get("/write-targets/{dataset_name}")
async def get_write_target(dataset_name: str, principal: Principal = Depends(current_principal)) -> dict:
    target = await write_target_registry.get_write_target(app.state.pool, principal.tenant_id, dataset_name)
    if target is None:
        raise HTTPException(status_code=404, detail=f"no write target registered for dataset {dataset_name!r}")
    return target


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
    try:
        return await write_target_registry.apply_write(
            app.state.pool, SOURCE_DB_URL,
            tenant_id=principal.tenant_id, dataset_name=dataset_name, instance_id=instance_id, edits=request.edits,
        )
    except write_target_registry.UnknownWriteTargetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except write_target_registry.InstanceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except write_target_registry.WriteTargetConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
