"""Identity Platform — Tenant, Workspace, and Principal management.

Manages authentication, tenant/workspace/principal registration, and token issuance.
"""

from __future__ import annotations

import logging
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
from holon_common.readiness import check_kafka_producer, check_opa, check_postgres, check_spicedb, report_ready
from holon_common.service_runtime import (
    boot_postgres,
    reset_durable_audit,
    start_outbox_relay,
    stop_outbox,
    wire_authz,
)

from . import deps, scim
from .deps import (
    DB_URL,
    KAFKA_BOOTSTRAP,
    OPA_URL,
    OTLP_ENDPOINT,
    SERVICE_NAME,
    SPICEDB_PRESHARED_KEY,
    SPICEDB_SCHEMA_PATH,
    SPICEDB_URL,
    TENANT_ID,
    WORKSPACE_ID,
)
from .routers import router as api_router
from .seed import ensure_instance_bootstrap
from .token_revocation import hydrate_local_denylist_from_db

configure_json_logging(SERVICE_NAME)
logger = logging.getLogger("identity")


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_production_posture(service_name=SERVICE_NAME)
    # Identity-owned tables live in app/migrations (0000_baseline + 0001–0002).
    # Shared holon_common helpers (audit/outbox) are included in 0000.
    await boot_postgres(
        app, db_url=DB_URL, migrations_dir=Path(__file__).parent / "migrations", deps_module=deps
    )

    await hydrate_local_denylist_from_db(app.state.pool)

    reset_durable_audit(app.state.pool)
    wire_authz(
        app,
        spicedb_url=SPICEDB_URL,
        spicedb_preshared_key=SPICEDB_PRESHARED_KEY,
        opa_url=OPA_URL,
        deps_module=deps,
    )
    await retry_with_backoff(
        lambda: app.state.authz.write_schema(Path(SPICEDB_SCHEMA_PATH).read_text()),
        what="identity authz schema",
    )
    await retry_with_backoff(
        lambda: ensure_instance_bootstrap(
            app.state.pool, app.state.authz, tenant_id=TENANT_ID, workspace_id=WORKSPACE_ID
        ),
        what="identity empty-instance bootstrap",
    )

    outbox_handle = await start_outbox_relay(app.state.pool, kafka_bootstrap=KAFKA_BOOTSTRAP)
    app.state.producer = outbox_handle.producer
    deps.producer = outbox_handle.producer

    yield

    await stop_outbox(outbox_handle)
    await app.state.authz.aclose()
    await app.state.pool.close()


app = FastAPI(title="Holon — Identity Platform", lifespan=lifespan)
instrument_cors(app)
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
instrument_correlation(app)
install_error_handlers(app, service_name=SERVICE_NAME)
app.include_router(scim.router, prefix="/scim/v2")
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
        ]
    )
