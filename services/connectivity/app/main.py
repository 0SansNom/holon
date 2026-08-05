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
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import asyncpg
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from holon_common import (
    EventActor,
    EventEnvelope,
    EventProducer,
    Principal,
    build_urn,
    configure_json_logging,
    create_pool,
    instrument_metrics,
    instrument_tracing,
    make_principal_dependency,
    outbox,
)

from . import connector, file_connector, iceberg_writer, mongo_connector, plugin_registry, rest_connector, stream_connector

SERVICE_NAME = "connectivity-platform"
configure_json_logging(SERVICE_NAME)

TENANT_ID = os.environ["HOLON_TENANT_ID"]
WORKSPACE_ID = os.environ["HOLON_WORKSPACE_ID"]
JWT_SECRET = os.environ["HOLON_JWT_SECRET"]
DB_URL = os.environ["HOLON_DB_URL"]
SOURCE_DB_URL = os.environ["HOLON_SOURCE_DB_URL"]
MONGO_URL = os.environ["HOLON_MONGO_URL"]
REVIEWS_API_URL = os.environ["HOLON_REVIEWS_API_URL"]
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

# The streaming connector has no `Principal` to attribute events to —
# nothing called an API to trigger it — so it acts under its own
# service-account identity, same pattern as Knowledge's
# `knowledge-saga-orchestrator` (`actions.py`).
STREAM_INGEST_URN = build_urn(TENANT_ID, "global", "service-account", "connectivity-stream-ingest")

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
    async with app.state.pool.acquire() as conn:
        await conn.execute(_DDL)
        await outbox.ensure_schema(conn)
        await plugin_registry.ensure_schema(conn)

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

    yield

    stream_task.cancel()
    relay_task.cancel()
    await app.state.producer.stop()
    await app.state.pool.close()


app = FastAPI(title="Holon — Connectivity Platform", lifespan=lifespan)
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
current_principal = make_principal_dependency(JWT_SECRET)


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
    return {"status": "ok"}


async def _finalize_sync(
    *,
    connector_urn: str,
    dataset_name: str,
    result: iceberg_writer.IcebergWriteResult,
    started_at: datetime,
    finished_at: datetime,
    actor: EventActor,
) -> SyncResult:
    """Shared by `run_sync` and `stream_connector`'s background task —
    event construction, `sync_run` bookkeeping, and outbox enqueue
    don't care which triggered the sync, only what it produced.
    """
    dataset_urn = build_urn(TENANT_ID, WORKSPACE_ID, "dataset", dataset_name)
    dataset_version_urn = build_urn(TENANT_ID, WORKSPACE_ID, "dataset-version", str(result.snapshot_id))
    event_id = uuid.uuid4().hex

    event = EventEnvelope(
        event_id=event_id,
        event_type="connectivity.sync.completed",
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        aggregate_type="Connector",
        aggregate_id=connector_urn,
        correlation_id=event_id,
        partition_key=f"{TENANT_ID}/{dataset_urn}",
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
                TENANT_ID,
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


@app.post("/sync", response_model=SyncResult)
async def run_sync(request: SyncRequest = SyncRequest(), principal: Principal = Depends(current_principal)) -> SyncResult:
    dataset_name = request.dataset
    spec = DATASET_READERS.get(dataset_name)
    if spec is not None:
        connector_urn = spec["connector_urn"]
        read = spec["read"]
    else:
        # falls through to a registered plugin instead of a
        # hardcoded reader; this branch is the entire amount of dispatch
        # code any plugin, present or future, ever needs.
        plugin = await plugin_registry.load_active_plugin_for_dataset(app.state.pool, dataset_name)
        if plugin is None:
            raise HTTPException(status_code=404, detail=f"unknown dataset: {dataset_name}")
        connector_urn = build_urn(TENANT_ID, "global", "connector", f"plugin-{plugin.manifest.name}")
        read = plugin.fetch

    started_at = datetime.now(timezone.utc)
    rows = await read()
    result = await asyncio.to_thread(iceberg_writer.write_snapshot, rows, dataset_name, **ICEBERG_CONFIG)
    finished_at = datetime.now(timezone.utc)

    actor = EventActor(type=principal.type, urn=principal.urn, on_behalf_of=principal.on_behalf_of)
    return await _finalize_sync(
        connector_urn=connector_urn,
        dataset_name=dataset_name,
        result=result,
        started_at=started_at,
        finished_at=finished_at,
        actor=actor,
    )


@app.get("/syncs")
async def list_syncs(principal: Principal = Depends(current_principal)) -> list[dict]:
    rows = await app.state.pool.fetch(
        "SELECT * FROM sync_run WHERE tenant_id = $1 ORDER BY id DESC", principal.tenant_id
    )
    return [dict(row) for row in rows]


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


# One exception: a connector never writes back to its source, EXCEPT
# as part of an already-approved, audited ontology Action. This endpoint
# exists only to be called by Automation's Workflow Engine — the
# saga orchestrator for `Customer.closeAccount` — never by a connector's
# own sync path.
#
# `current_principal` alone proves a valid tenant-scoped JWT, which any
# authenticated principal has — it does NOT prove the caller is the workflow engine.
CLOSE_ACCOUNT_FAILURE_SENTINEL = "__simulate_failure__"
WORKFLOW_ENGINE_URN = build_urn(TENANT_ID, "global", "service-account", "automation-workflow-engine")


class CloseAccountRequest(BaseModel):
    reason: str


def _require_workflow_engine(principal: Principal) -> None:
    if principal.type != "service_account" or principal.urn != WORKFLOW_ENGINE_URN:
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
