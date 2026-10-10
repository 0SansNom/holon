"""Connectivity Platform — Connector execution and ingestion.

Executes registered connectors and lands data in the Iceberg raw zone.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from holon_common import (
    assert_production_posture,
    configure_json_logging,
    install_error_handlers,
    instrument_cors,
    instrument_metrics,
    instrument_tracing,
    retry_with_backoff,
)
from holon_common.correlation import instrument_correlation
from holon_common.readiness import (
    check_iceberg_catalog,
    check_kafka_producer,
    check_opa,
    check_postgres,
    check_spicedb,
    report_ready,
)
from holon_common.service_runtime import (
    boot_postgres,
    reset_durable_audit,
    start_outbox_relay,
    start_principal_status,
    stop_outbox,
    stop_principal_status,
    wire_authz,
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
    # Connectivity-owned tables live in app/migrations (0000_baseline + 0001).
    # Shared holon_common helpers (audit/outbox) are included in 0000.
    await boot_postgres(
        app, db_url=DB_URL, migrations_dir=Path(__file__).parent / "migrations", deps_module=deps
    )
    reset_durable_audit(app.state.pool)
    wire_authz(
        app,
        spicedb_url=SPICEDB_URL,
        spicedb_preshared_key=SPICEDB_PRESHARED_KEY,
        opa_url=OPA_URL,
        deps_module=deps,
    )
    await retry_with_backoff(
        lambda: ensure_authz_seeded(app.state.authz, app.state.pool),
        what="connectivity authz seed",
    )

    outbox_handle = await start_outbox_relay(app.state.pool, kafka_bootstrap=KAFKA_BOOTSTRAP)
    app.state.producer = outbox_handle.producer

    deps.kafka_stream_tasks = {}
    for source in await kafka_stream_registry.list_all_active(app.state.pool):
        _spawn_kafka_stream_task(source)

    scheduler_task = asyncio.create_task(run_scheduler_forever(app.state.pool))

    status_handle = await start_principal_status(
        kafka_bootstrap=KAFKA_BOOTSTRAP,
        service_name=SERVICE_NAME,
        authz=app.state.authz,
        dlq_producer=app.state.producer,
    )

    yield

    await stop_principal_status(status_handle)
    scheduler_task.cancel()
    for task in deps.kafka_stream_tasks.values():
        task.cancel()
    await stop_outbox(outbox_handle)
    await app.state.authz.aclose()
    await app.state.pool.close()


app = FastAPI(title="Holon — Connectivity Platform", lifespan=lifespan)
instrument_cors(app)  # Experience BFF proxies /api/connectivity; CORS kept for local tooling
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
instrument_correlation(app)
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
