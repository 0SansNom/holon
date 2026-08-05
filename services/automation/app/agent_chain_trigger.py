"""Automation Platform — the event-triggered agent-session trigger that
implements loop detection for agent chaining.

Reacts to Intelligence's `intelligence.agent.session_completed` event and,
when the completed session opted into `chain_trigger`, spawns a *new* agent
session on Intelligence — threading `causation_id`/`causation_depth` forward.

Deliberately opt-in, not automatic for every session: `chain_trigger`
must be set to `true` by whoever created the *first* session in a
chain. `agent_runtime.create_session` is the authoritative circuit breaker
(refuses past `max_chain_depth`); the depth check here avoids a wasted HTTP round trip.
`max_chain_depth` is threaded hop-to-hop from the very first session.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from holon_common import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    EventConsumer,
    Principal,
    build_urn,
    issue_token,
)

logger = logging.getLogger("automation.agent_chain_trigger")

_TIMEOUT_SECONDS = 10.0
TRIGGERED_WORKFLOW_ENGINE_NAME = "automation-agent-chain-trigger"


def _chain_trigger_principal(tenant_id: str) -> Principal:
    return Principal(
        urn=build_urn(tenant_id, "global", "service-account", TRIGGERED_WORKFLOW_ENGINE_NAME),
        type="service_account",
        tenant_id=tenant_id,
        display_name="Automation Agent Chain Trigger",
    )


async def _spawn_next_session(
    client: httpx.AsyncClient,
    breaker: CircuitBreaker,
    *,
    tenant_id: str,
    causation_id: str,
    causation_depth: int,
    max_chain_depth: int,
    intelligence_url: str,
    jwt_secret: str,
) -> None:
    token = issue_token(_chain_trigger_principal(tenant_id), jwt_secret, ttl_seconds=60)
    headers = {"Authorization": f"Bearer {token}"}

    async def _create_session() -> dict:
        response = await client.post(
            f"{intelligence_url}/sessions",
            headers=headers,
            json={
                "causation_id": causation_id,
                "causation_depth": causation_depth,
                "chain_trigger": True,
                "max_chain_depth": max_chain_depth,
            },
        )
        response.raise_for_status()
        return response.json()

    session = await breaker.call(_create_session)

    async def _run_turn() -> dict:
        response = await client.post(
            f"{intelligence_url}/sessions/{session['urn']}/turns",
            headers=headers,
            json={
                "message": (
                    f"Automated chain-trigger turn at depth {causation_depth}. "
                    "Reply with a short acknowledgement; do not use any tools."
                )
            },
        )
        response.raise_for_status()
        return response.json()

    await breaker.call(_run_turn)


async def consume_events(consumer: EventConsumer, *, intelligence_url: str, jwt_secret: str) -> None:
    """Consumes Intelligence's bus (topic `intelligence`) — the
    **Trigger**, same literal meaning as `workflow.consume_events`'s own
    docstring: "this event starts this [agent session]."
    """
    async with httpx.AsyncClient(
        timeout=_TIMEOUT_SECONDS, limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
    ) as client:
        breaker = CircuitBreaker(name="intelligence-chain-trigger", failure_threshold=5, cooldown_seconds=30.0)

        await consumer.start()
        async for event in consumer:
            try:
                if event.event_type != "intelligence.agent.session_completed":
                    continue
                if not event.payload.get("chain_trigger"):
                    continue
                depth = event.payload.get("causation_depth", 0)
                max_chain_depth = event.payload.get("max_chain_depth", 10)
                next_depth = depth + 1
                if next_depth > max_chain_depth:
                    # `max_chain_depth` is inclusive (the just-completed
                    # session at `depth` was the last one allowed) — this
                    # mirrors `agent_runtime.create_session`'s own
                    # authoritative check exactly, just earlier, to skip
                    # a wasted HTTP round trip.
                    logger.info(
                        "agent chain cut off after depth %d (max_chain_depth=%d) for session %s — loop guard",
                        depth, max_chain_depth, event.payload.get("session_urn"),
                    )
                    continue
                await _spawn_next_session(
                    client, breaker,
                    tenant_id=event.tenant_id,
                    causation_id=event.event_id,
                    causation_depth=next_depth,
                    max_chain_depth=max_chain_depth,
                    intelligence_url=intelligence_url,
                    jwt_secret=jwt_secret,
                )
            except (httpx.HTTPError, CircuitBreakerOpenError):
                logger.exception("agent chain trigger failed to spawn the next session for event %s", event.event_id)
