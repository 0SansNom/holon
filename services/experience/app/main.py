"""Experience Platform — Serves the React SPA and Application Builder API."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from holon_common import (
    CircuitBreaker,
    PermissionClient,
    assert_production_posture,
    configure_json_logging,
    create_pool,
    install_error_handlers,
    instrument_cors,
    instrument_metrics,
    instrument_tracing,
    retry_with_backoff,
    run_migrations,
)
from holon_common.audit import clear_durable_audit_hooks
from holon_common.audit_store import install_durable_audit
from holon_common.principal_status import (
    consume_identity_auth_events,
    hydrate_revocation_snapshot,
    make_principal_status_consumer,
    refresh_revocation_snapshot_forever,
)
from holon_common.readiness import check_kafka_bootstrap, check_opa, check_postgres, check_spicedb, report_ready

from . import application_builder, deps
from .deps import (
    DB_URL,
    KAFKA_BOOTSTRAP,
    OPA_URL,
    OTLP_ENDPOINT,
    SERVICE_NAME,
    SPICEDB_PRESHARED_KEY,
    SPICEDB_URL,
    TENANT_ID,
    WORKSPACE_ID,
    WORKSPACE_URN,
)
from .routers import router as api_router

configure_json_logging(SERVICE_NAME)

_TIMEOUT_SECONDS = 5.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_production_posture(service_name=SERVICE_NAME)
    app.state.client = httpx.AsyncClient(
        timeout=_TIMEOUT_SECONDS, limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
    )
    deps.client = app.state.client
    app.state.breaker = CircuitBreaker(name="experience-proxy", failure_threshold=5, cooldown_seconds=30.0)
    deps.breaker = app.state.breaker

    app.state.pool = await create_pool(DB_URL)
    deps.pool = app.state.pool
    # Experience-owned tables live in app/migrations/0000_baseline.sql.
    await run_migrations(app.state.pool, Path(__file__).parent / "migrations")

    clear_durable_audit_hooks()
    install_durable_audit(app.state.pool)

    app.state.authz = PermissionClient(SPICEDB_URL, SPICEDB_PRESHARED_KEY, OPA_URL)
    deps.authz = app.state.authz

    async def _seed_application_authz() -> None:
        backfilled = await application_builder.backfill_urns(
            app.state.pool, tenant_id=TENANT_ID, workspace_id=WORKSPACE_ID,
        )
        for name in backfilled:
            await app.state.authz.write_relationship(
                resource_type="application",
                resource_urn=application_builder.application_urn(TENANT_ID, WORKSPACE_ID, name),
                relation="parent_workspace",
                subject_type="workspace",
                subject_urn=WORKSPACE_URN,
            )

    await retry_with_backoff(_seed_application_authz, what="experience authz seed")

    status_consumer = make_principal_status_consumer(KAFKA_BOOTSTRAP, service_name=SERVICE_NAME)
    status_task = asyncio.create_task(consume_identity_auth_events(status_consumer, authz=app.state.authz))
    await retry_with_backoff(hydrate_revocation_snapshot, what="identity revocation snapshot")
    revocation_refresh_task = asyncio.create_task(refresh_revocation_snapshot_forever())

    yield
    revocation_refresh_task.cancel()
    status_task.cancel()
    await status_consumer.stop()
    await app.state.pool.close()
    await app.state.client.aclose()


app = FastAPI(title="Holon — Experience Platform", lifespan=lifespan)
instrument_cors(app)
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
install_error_handlers(app, service_name=SERVICE_NAME)


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
            check_kafka_bootstrap(KAFKA_BOOTSTRAP),
        ]
    )


# Probes are registered before the SPA catch-all inside `api_router`.
app.include_router(api_router)
