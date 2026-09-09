"""Identity Platform — Tenant, Workspace, and Principal management.

Manages authentication, tenant/workspace/principal registration, and token issuance.
"""

from __future__ import annotations

import asyncio
import logging
import os
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
from holon_common.readiness import check_kafka_producer, check_opa, check_postgres, check_spicedb, report_ready

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
    app.state.pool = await create_pool(DB_URL)
    deps.pool = app.state.pool
    # Identity-owned tables live in app/migrations (0000_baseline + 0001–0002).
    # Shared holon_common helpers (audit/outbox) are included in 0000.
    await run_migrations(app.state.pool, Path(__file__).parent / "migrations")

    await hydrate_local_denylist_from_db(app.state.pool)

    clear_durable_audit_hooks()
    install_durable_audit(app.state.pool)

    app.state.authz = PermissionClient(SPICEDB_URL, SPICEDB_PRESHARED_KEY, OPA_URL)
    deps.authz = app.state.authz
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

    app.state.producer = EventProducer(KAFKA_BOOTSTRAP)
    deps.producer = app.state.producer
    await app.state.producer.start()
    relay_task = asyncio.create_task(outbox.relay_forever(app.state.pool, app.state.producer, dlq_producer=app.state.producer))

    yield

    relay_task.cancel()
    await app.state.producer.stop()
    await app.state.authz.aclose()
    await app.state.pool.close()


app = FastAPI(title="Holon — Identity Platform", lifespan=lifespan)
instrument_cors(app)
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
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
