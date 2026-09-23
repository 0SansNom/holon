"""Connectivity Platform — Connector execution and ingestion.

Executes registered connectors and lands data in the Iceberg raw zone.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from holon_common import (
    EventProducer,
    PermissionClient,
    assert_production_posture,
    configure_json_logging,
    create_pool,
    install_error_handlers,
    instrument_cors,
    instrument_metrics,
    instrument_tracing,
    outbox,
    retry_with_backoff,
    run_migrations,
)
from holon_common.audit import clear_durable_audit_hooks
from holon_common.audit_store import install_durable_audit
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

from . import deps, kafka_stream_registry
from .authz_seed import ensure_authz_seeded
from .deps import (
    DB_URL,
    ICEBERG_CONFIG,
    KAFKA_BOOTSTRAP,
    OPA_URL,
    OTLP_ENDPOINT,
    SERVICE_NAME,
    SPICEDB_PRESHARED_KEY,
    SPICEDB_URL,
)
from .ingest import _is_quiesced, _spawn_kafka_stream_task, run_scheduler_forever
from .routers import router as api_router

configure_json_logging(SERVICE_NAME)


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_production_posture(service_name=SERVICE_NAME)
    app.state.pool = await create_pool(DB_URL)
    deps.pool = app.state.pool
    # Connectivity-owned tables live in app/migrations (0000_baseline + 0001).
    # Shared holon_common helpers (audit/outbox) are included in 0000.
    await run_migrations(app.state.pool, Path(__file__).parent / "migrations")

    clear_durable_audit_hooks()
    install_durable_audit(app.state.pool)

    app.state.authz = PermissionClient(SPICEDB_URL, SPICEDB_PRESHARED_KEY, OPA_URL)
    deps.authz = app.state.authz
    await retry_with_backoff(
        lambda: ensure_authz_seeded(app.state.authz, app.state.pool),
        what="connectivity authz seed",
    )

    app.state.producer = EventProducer(KAFKA_BOOTSTRAP)
    await app.state.producer.start()
    relay_task = asyncio.create_task(outbox.relay_forever(app.state.pool, app.state.producer, dlq_producer=app.state.producer))

    deps.kafka_stream_tasks = {}
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
    for task in deps.kafka_stream_tasks.values():
        task.cancel()
    relay_task.cancel()
    await status_consumer.stop()
    await app.state.producer.stop()
    await app.state.authz.aclose()
    await app.state.pool.close()


app = FastAPI(title="Holon — Connectivity Platform", lifespan=lifespan)
instrument_cors(app)  # Experience BFF proxies /api/connectivity; CORS kept for local tooling
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
install_error_handlers(app, service_name=SERVICE_NAME)
app.include_router(api_router)


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
