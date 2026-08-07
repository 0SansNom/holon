"""Automation Platform — Workflow Engine.

The Workflow Engine: owns saga orchestration and its
persisted execution record for Actions whose approval needs a step
outside Knowledge. See `workflow.py`'s module docstring for details.

Service-to-service only in this build — no end-user reads go through
Automation directly, so there's no PDP integration here, just JWT auth
(`make_principal_dependency`), same as Connectivity's internal endpoints.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException

from holon_common import (
    EventConsumer,
    EventProducer,
    Principal,
    configure_json_logging,
    create_pool,
    install_error_handlers,
    instrument_metrics,
    instrument_tracing,
    make_principal_dependency,
    outbox,
)

from . import agent_chain_trigger, workflow

SERVICE_NAME = "automation-platform"
configure_json_logging(SERVICE_NAME)

TENANT_ID = os.environ["HOLON_TENANT_ID"]
WORKSPACE_ID = os.environ["HOLON_WORKSPACE_ID"]
JWT_SECRET = os.environ["HOLON_JWT_SECRET"]
DB_URL = os.environ["HOLON_DB_URL"]
KAFKA_BOOTSTRAP = os.environ["HOLON_KAFKA_BOOTSTRAP"]
CONNECTIVITY_URL = os.environ["HOLON_CONNECTIVITY_URL"]
KNOWLEDGE_URL = os.environ["HOLON_KNOWLEDGE_URL"]
INTELLIGENCE_URL = os.environ["HOLON_INTELLIGENCE_URL"]
OTLP_ENDPOINT = os.environ["HOLON_OTLP_ENDPOINT"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool(DB_URL)
    async with app.state.pool.acquire() as conn:
        await workflow.ensure_schema(conn)
        await outbox.ensure_schema(conn)

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

    # Loop-detection trigger — see agent_chain_trigger.py's module
    # docstring. Own consumer group: logically a distinct trigger from the
    # saga workflow above, on a different topic entirely.
    agent_chain_consumer = EventConsumer(
        KAFKA_BOOTSTRAP,
        topics=["intelligence"],
        group_id="automation-platform-agent-chain-trigger",
        dlq_producer=app.state.producer,
    )
    agent_chain_task = asyncio.create_task(
        agent_chain_trigger.consume_events(agent_chain_consumer, intelligence_url=INTELLIGENCE_URL, jwt_secret=JWT_SECRET)
    )

    yield

    agent_chain_task.cancel()
    consume_task.cancel()
    relay_task.cancel()
    await agent_chain_consumer.stop()
    await consumer.stop()
    await app.state.producer.stop()
    await app.state.pool.close()


app = FastAPI(title="Holon — Automation Platform", lifespan=lifespan)
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
install_error_handlers(app, service_name=SERVICE_NAME)
current_principal = make_principal_dependency(JWT_SECRET)


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


@app.get("/workflows/{approval_id}")
async def get_workflow_execution(approval_id: int, principal: Principal = Depends(current_principal)) -> dict:
    """Automation's own execution record — proof that Automation, not Knowledge,
    tracks saga orchestration state.
    """
    execution = await workflow.get_workflow_execution(app.state.pool, approval_id)
    if execution is None:
        raise HTTPException(status_code=404, detail=f"no workflow execution found for approval {approval_id}")
    return execution
