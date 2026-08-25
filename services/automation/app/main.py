"""Automation Platform — Workflow Engine.

Orchestrates saga execution and persisted execution records for actions.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI

from holon_common import (
    HolonError,
    EventConsumer,
    EventProducer,
    Principal,
    active_jwt,
    assert_production_posture,
    configure_json_logging,
    create_pool,
    install_error_handlers,
    instrument_metrics,
    instrument_tracing,
    make_principal_dependency,
    outbox,
    run_migrations,
)
from holon_common.audit import clear_durable_audit_hooks
from holon_common.audit_store import (
    ensure_schema as ensure_audit_schema,
    install_durable_audit,
    list_events_page,
)

from holon_common.principal_status import consume_identity_auth_events, make_principal_status_consumer

from . import agent_chain_trigger, workflow

SERVICE_NAME = "automation-platform"
configure_json_logging(SERVICE_NAME)

TENANT_ID = os.environ["HOLON_TENANT_ID"]
WORKSPACE_ID = os.environ["HOLON_WORKSPACE_ID"]
JWT_SECRET, JWT_ACTIVE_KID, JWT_SECRETS = active_jwt()
DB_URL = os.environ["HOLON_DB_URL"]
KAFKA_BOOTSTRAP = os.environ["HOLON_KAFKA_BOOTSTRAP"]
CONNECTIVITY_URL = os.environ["HOLON_CONNECTIVITY_URL"]
KNOWLEDGE_URL = os.environ["HOLON_KNOWLEDGE_URL"]
INTELLIGENCE_URL = os.environ["HOLON_INTELLIGENCE_URL"]
OTLP_ENDPOINT = os.environ.get("HOLON_OTLP_ENDPOINT", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_production_posture(service_name=SERVICE_NAME)
    app.state.pool = await create_pool(DB_URL)
    async with app.state.pool.acquire() as conn:
        await workflow.ensure_schema(conn)
        await ensure_audit_schema(conn)
        await outbox.ensure_schema(conn)
    await run_migrations(app.state.pool, Path(__file__).parent / "migrations")

    clear_durable_audit_hooks()
    install_durable_audit(app.state.pool)

    app.state.producer = EventProducer(KAFKA_BOOTSTRAP)
    await app.state.producer.start()
    relay_task = asyncio.create_task(outbox.relay_forever(app.state.pool, app.state.producer, dlq_producer=app.state.producer))

    consumer = EventConsumer(
        KAFKA_BOOTSTRAP, topics=["knowledge"], group_id="automation-platform", dlq_producer=app.state.producer
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
        agent_chain_trigger.consume_events(agent_chain_consumer, intelligence_url=INTELLIGENCE_URL, jwt_secret=JWT_SECRET)
    )

    status_consumer = make_principal_status_consumer(
        KAFKA_BOOTSTRAP, service_name=SERVICE_NAME, dlq_producer=app.state.producer
    )
    status_task = asyncio.create_task(consume_identity_auth_events(status_consumer))

    yield

    status_task.cancel()
    agent_chain_task.cancel()
    consume_task.cancel()
    relay_task.cancel()
    await status_consumer.stop()
    await agent_chain_consumer.stop()
    await consumer.stop()
    await app.state.producer.stop()
    await app.state.pool.close()


app = FastAPI(title="Holon — Automation Platform", lifespan=lifespan)
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
install_error_handlers(app, service_name=SERVICE_NAME)
current_principal = make_principal_dependency(JWT_SECRET, secrets=JWT_SECRETS)


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


@app.get("/audit-events")
async def list_automation_audit_events(
    principal: Principal = Depends(current_principal),
    category: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    outcome: str | None = None,
    pageSize: int | None = None,
    pageToken: str | None = None,
) -> dict:
    """Durable Automation audit (workflows, agent-chain triggers)."""
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


@app.get("/workflows/{approval_id}")
async def get_workflow_execution(approval_id: int, principal: Principal = Depends(current_principal)) -> dict:
    """Fetch workflow execution record by approval ID."""
    execution = await workflow.get_workflow_execution(app.state.pool, approval_id)
    if execution is None or execution.get("tenant_id") != principal.tenant_id:
        raise HolonError.not_found(
            "WorkflowExecutionNotFound",
            f"no workflow execution found for approval {approval_id}",
            approval_id=approval_id,
        )
    return execution
