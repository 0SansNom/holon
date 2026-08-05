"""Automation Platform — Workflow Engine.

Sagas are implemented by the Workflow Engine (Automation), with persisted state
and declared compensation steps.
Automation owns orchestration and the persisted execution record;
each step's actual business mutation still lives in its owning service
(Knowledge's local overlay, Connectivity's source-system write).

Scoping:

- **Workflow**: `WORKFLOW_DEFINITIONS` below — one workflow, one step,
  not a general multi-step DAG compiler.
- **Trigger**: `consume_events`'s filter is the trigger — "this event starts this workflow".
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import httpx

from holon_common import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    EventActor,
    EventConsumer,
    EventEnvelope,
    EventProducer,
    Principal,
    build_urn,
    issue_token,
    outbox,
)

logger = logging.getLogger("automation.workflow")

_TIMEOUT_SECONDS = 10.0

# The Workflow resource — one entry, matching the one Action in this
# build whose approval needs an external step.
WORKFLOW_DEFINITIONS = {"Customer.closeAccount": {"target_service": "connectivity"}}

WORKFLOW_ENGINE_URN_NAME = "automation-workflow-engine"

DDL = """
CREATE TABLE IF NOT EXISTS workflow_execution (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    approval_id BIGINT NOT NULL,
    action_name TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


def _workflow_engine_principal(tenant_id: str) -> Principal:
    return Principal(
        urn=build_urn(tenant_id, "global", "service-account", WORKFLOW_ENGINE_URN_NAME),
        type="service_account",
        tenant_id=tenant_id,
        display_name="Automation Workflow Engine",
    )


async def get_workflow_execution(pool: asyncpg.Pool, approval_id: int) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM workflow_execution WHERE approval_id = $1 ORDER BY id DESC LIMIT 1", approval_id)
    return dict(row) if row else None


async def _notify_source_system(
    client: httpx.AsyncClient,
    breaker: CircuitBreaker,
    *,
    tenant_id: str,
    customer_id: int,
    reason: str,
    connectivity_url: str,
    jwt_secret: str,
) -> None:
    token = issue_token(_workflow_engine_principal(tenant_id), jwt_secret, ttl_seconds=60)

    async def _do() -> httpx.Response:
        response = await client.post(
            f"{connectivity_url}/source/customers/{customer_id}/close-account",
            json={"reason": reason},
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return response

    await breaker.call(_do)


async def _request_compensation(
    client: httpx.AsyncClient,
    breaker: CircuitBreaker,
    *,
    tenant_id: str,
    approval_id: int,
    error: str,
    knowledge_url: str,
    jwt_secret: str,
) -> None:
    """Automation can't touch Knowledge's own tables directly (separate
    database, separate service) — compensating Step 1 is Knowledge's own
    concern (it reverts *its* overlay), so Automation just tells it to,
    the same way it tells Connectivity to apply Step 2.
    """
    token = issue_token(_workflow_engine_principal(tenant_id), jwt_secret, ttl_seconds=60)

    async def _do() -> httpx.Response:
        response = await client.post(
            f"{knowledge_url}/internal/approvals/{approval_id}/compensate",
            json={"error": error},
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return response

    await breaker.call(_do)


def _publish_event(*, tenant_id: str, workspace_id: str, instance_urn: str, action_name: str, approval_id: int) -> EventEnvelope:
    event_id = uuid.uuid4().hex
    actor = _workflow_engine_principal(tenant_id)
    return EventEnvelope(
        event_id=event_id,
        event_type="automation.workflow.completed",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        aggregate_type="Workflow",
        aggregate_id=instance_urn,
        correlation_id=event_id,
        partition_key=f"{tenant_id}/{instance_urn}",
        producer="automation-platform@0.1.0",
        actor=EventActor(type=actor.type, urn=actor.urn, on_behalf_of=actor.on_behalf_of),
        payload={"workflow_name": action_name, "approval_id": approval_id, "instance_urn": instance_urn},
    )


async def _run_workflow(
    pool: asyncpg.Pool,
    client: httpx.AsyncClient,
    connectivity_breaker: CircuitBreaker,
    knowledge_breaker: CircuitBreaker,
    *,
    tenant_id: str,
    workspace_id: str,
    approval_id: int,
    action_name: str,
    instance_urn: str,
    customer_id: int,
    reason: str,
    connectivity_url: str,
    knowledge_url: str,
    jwt_secret: str,
) -> None:
    execution_id = await pool.fetchval(
        "INSERT INTO workflow_execution (tenant_id, approval_id, action_name, status) VALUES ($1, $2, $3, 'running') RETURNING id",
        tenant_id, approval_id, action_name,
    )

    try:
        await _notify_source_system(
            client, connectivity_breaker,
            tenant_id=tenant_id, customer_id=customer_id, reason=reason,
            connectivity_url=connectivity_url, jwt_secret=jwt_secret,
        )
    except (httpx.HTTPError, CircuitBreakerOpenError) as exc:
        error = str(exc)
        await pool.execute(
            "UPDATE workflow_execution SET status = 'compensated', error = $1, updated_at = now() WHERE id = $2",
            error, execution_id,
        )
        try:
            await _request_compensation(
                client, knowledge_breaker,
                tenant_id=tenant_id, approval_id=approval_id, error=error,
                knowledge_url=knowledge_url, jwt_secret=jwt_secret,
            )
        except (httpx.HTTPError, CircuitBreakerOpenError):
            logger.exception("compensation callback to Knowledge failed for approval %s", approval_id)
        return

    await pool.execute(
        "UPDATE workflow_execution SET status = 'completed', updated_at = now() WHERE id = $1", execution_id
    )
    async with pool.acquire() as conn, conn.transaction():
        await outbox.enqueue(
            conn,
            _publish_event(
                tenant_id=tenant_id, workspace_id=workspace_id, instance_urn=instance_urn,
                action_name=action_name, approval_id=approval_id,
            ),
        )


def _customer_id_from_instance_urn(instance_urn: str) -> int:
    return int(instance_urn.rsplit("/", 1)[-1])


async def consume_events(
    pool: asyncpg.Pool,
    consumer: EventConsumer,
    *,
    workspace_id: str,
    connectivity_url: str,
    knowledge_url: str,
    jwt_secret: str,
) -> None:
    """Consumes Knowledge's bus (topic `knowledge`), triggered — the
    **Trigger** resource, in the most literal sense — by
    `knowledge.action.invoked` events whose action has a registered
    Workflow and an `approval_id` (i.e. went through human-in-the-loop
    approval, not an immediately-applied low-risk Action).
    """
    # Bulkhead: one long-lived client for this task's whole
    # lifetime, not a throwaway `httpx.AsyncClient()` per workflow run;
    # a circuit breaker per downstream dependency so a stuck Connectivity
    # doesn't also degrade calls to Knowledge and vice versa.
    async with httpx.AsyncClient(
        timeout=_TIMEOUT_SECONDS, limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
    ) as client:
        connectivity_breaker = CircuitBreaker(name="connectivity-close-account", failure_threshold=5, cooldown_seconds=30.0)
        knowledge_breaker = CircuitBreaker(name="knowledge-compensate", failure_threshold=5, cooldown_seconds=30.0)

        await consumer.start()
        async for event in consumer:
            try:
                if event.event_type != "knowledge.action.invoked":
                    continue
                action_name = event.payload["action_name"]
                approval_id = event.payload.get("approval_id")
                if action_name not in WORKFLOW_DEFINITIONS or approval_id is None:
                    continue

                instance_urn = event.payload["instance_urn"]
                await _run_workflow(
                    pool, client, connectivity_breaker, knowledge_breaker,
                    tenant_id=event.tenant_id,
                    workspace_id=workspace_id,
                    approval_id=approval_id,
                    action_name=action_name,
                    instance_urn=instance_urn,
                    customer_id=_customer_id_from_instance_urn(instance_urn),
                    reason=event.payload.get("reason", ""),
                    connectivity_url=connectivity_url,
                    knowledge_url=knowledge_url,
                    jwt_secret=jwt_secret,
                )
            except Exception:
                logger.exception("failed to process workflow trigger for event %s, skipping", event.event_id)
            await consumer.commit()
