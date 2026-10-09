"""Automation Platform — Workflow Engine.

Orchestrates saga execution and persisted execution records for actions.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from holon_common import (
    EventConsumer,
    assert_production_posture,
    configure_json_logging,
    install_error_handlers,
    instrument_metrics,
    instrument_tracing,
)
from holon_common.correlation import instrument_correlation
from holon_common.readiness import check_kafka_producer, check_opa, check_postgres, check_spicedb, report_ready
from holon_common.service_runtime import (
    boot_postgres,
    reset_durable_audit,
    start_outbox_relay,
    start_principal_status,
    stop_outbox,
    stop_principal_status,
    wire_authz,
)

from . import agent_chain_trigger, deps, workflow
from .deps import (
    CONNECTIVITY_URL,
    DB_URL,
    INTELLIGENCE_URL,
    JWT_SECRET,
    KAFKA_BOOTSTRAP,
    KNOWLEDGE_URL,
    OPA_URL,
    OTLP_ENDPOINT,
    SERVICE_NAME,
    SPICEDB_PRESHARED_KEY,
    SPICEDB_URL,
    WORKSPACE_ID,
)
from .routers import router as api_router

configure_json_logging(SERVICE_NAME)


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_production_posture(service_name=SERVICE_NAME)
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

    outbox_handle = await start_outbox_relay(app.state.pool, kafka_bootstrap=KAFKA_BOOTSTRAP)
    app.state.producer = outbox_handle.producer

    consumer = EventConsumer(
        KAFKA_BOOTSTRAP,
        topics=["knowledge"],
        group_id="automation-platform",
        dlq_producer=app.state.producer,
    )
    consume_task = asyncio.create_task(
        workflow.consume_events(
            app.state.pool,
            consumer,
            workspace_id=WORKSPACE_ID,
            connectivity_url=CONNECTIVITY_URL,
            knowledge_url=KNOWLEDGE_URL,
            jwt_secret=JWT_SECRET,
        )
    )

    agent_chain_consumer = EventConsumer(
        KAFKA_BOOTSTRAP,
        topics=["intelligence"],
        group_id="automation-platform-agent-chain-trigger",
        dlq_producer=app.state.producer,
    )
    agent_chain_task = asyncio.create_task(
        agent_chain_trigger.consume_events(
            agent_chain_consumer, intelligence_url=INTELLIGENCE_URL, jwt_secret=JWT_SECRET
        )
    )

    status_handle = await start_principal_status(
        kafka_bootstrap=KAFKA_BOOTSTRAP,
        service_name=SERVICE_NAME,
        authz=app.state.authz,
        dlq_producer=app.state.producer,
    )

    yield

    await stop_principal_status(status_handle)
    agent_chain_task.cancel()
    consume_task.cancel()
    await agent_chain_consumer.stop()
    await consumer.stop()
    await stop_outbox(outbox_handle)
    await app.state.authz.aclose()
    await app.state.pool.close()


app = FastAPI(title="Holon — Automation Platform", lifespan=lifespan)
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
        ]
    )
