"""Automation Platform — the event-triggered agent-session trigger that
implements loop detection for agent chaining.

Reacts to Intelligence's `intelligence.agent.session_completed` event and,
when the completed session opted into `chain_trigger`, spawns a *new* agent
session on Intelligence — threading `causation_id`/`causation_depth` forward.

Chained hops authenticate as the shared `ingest-bot` agent (same principal
Intelligence / agentApp use), carrying forward `on_behalf_of` from the
completed session — not Automation's own service-account identity.

Deliberately opt-in, not automatic for every session: `chain_trigger`
must be set to `true` by whoever created the *first* session in a
chain. `agent_runtime.create_session` is the authoritative circuit breaker
(refuses past `max_chain_depth`); the depth check here avoids a wasted HTTP round trip.
`max_chain_depth` is threaded hop-to-hop from the very first session.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

from holon_common import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    EventConsumer,
    Principal,
    active_jwt,
    build_urn,
    issue_token,
)
from holon_common.audit import emit_audit

logger = logging.getLogger("automation.agent_chain_trigger")

_TIMEOUT_SECONDS = 10.0
# Local-name of the agent principal used for every chained hop. Override
# only if an operator provisions a different shared agent URN.
DEFAULT_CHAIN_AGENT_LOCAL_NAME = "ingest-bot"


def _mint(principal: Principal, jwt_secret: str, *, ttl_seconds: int = 60) -> str:
    try:
        secret, kid, secrets_map = active_jwt()
        return issue_token(principal, secret, ttl_seconds=ttl_seconds, kid=kid, secrets=secrets_map)
    except RuntimeError:
        return issue_token(principal, jwt_secret, ttl_seconds=ttl_seconds)


def chain_agent_local_name() -> str:
    return (os.environ.get("HOLON_CHAIN_TRIGGER_AGENT_LOCAL_NAME") or DEFAULT_CHAIN_AGENT_LOCAL_NAME).strip() or (
        DEFAULT_CHAIN_AGENT_LOCAL_NAME
    )


def chain_agent_principal(tenant_id: str, *, on_behalf_of: Optional[str] = None) -> Principal:
    """Principal for chained hops — ingest-bot by default, not Automation's SA."""
    local = chain_agent_local_name()
    return Principal(
        urn=build_urn(tenant_id, "global", "agent", local),
        type="agent",
        tenant_id=tenant_id,
        display_name="Ingest Bot" if local == DEFAULT_CHAIN_AGENT_LOCAL_NAME else local,
        on_behalf_of=on_behalf_of,
        country="FR",
    )


async def _spawn_next_session(
    client: httpx.AsyncClient,
    breaker: CircuitBreaker,
    *,
    tenant_id: str,
    causation_id: str,
    causation_depth: int,
    max_chain_depth: int,
    on_behalf_of: Optional[str],
    intelligence_url: str,
    jwt_secret: str,
) -> None:
    principal = chain_agent_principal(tenant_id, on_behalf_of=on_behalf_of)
    token = _mint(principal, jwt_secret, ttl_seconds=60)
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
    emit_audit(
        category="action",
        action="automation.agent_chain.spawned",
        outcome="success",
        tenant_id=tenant_id,
        actor_urn=chain_agent_principal(tenant_id, on_behalf_of=on_behalf_of).urn,
        actor_type="agent",
        resource_type="agent_session",
        resource_urn=session.get("urn"),
        extra={"causation_depth": causation_depth, "max_chain_depth": max_chain_depth},
    )


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
                    logger.info(
                        "agent chain cut off after depth %d (max_chain_depth=%d) for session %s — loop guard",
                        depth, max_chain_depth, event.payload.get("session_urn"),
                    )
                    emit_audit(
                        category="action",
                        action="automation.agent_chain.cutoff",
                        outcome="deny",
                        tenant_id=event.tenant_id,
                        resource_type="agent_session",
                        resource_urn=event.payload.get("session_urn"),
                        reason="max_chain_depth",
                        extra={"depth": depth, "max_chain_depth": max_chain_depth},
                    )
                    continue
                await _spawn_next_session(
                    client,
                    breaker,
                    tenant_id=event.tenant_id,
                    causation_id=event.event_id,
                    causation_depth=next_depth,
                    max_chain_depth=max_chain_depth,
                    on_behalf_of=event.payload.get("on_behalf_of"),
                    intelligence_url=intelligence_url,
                    jwt_secret=jwt_secret,
                )
            except (httpx.HTTPError, CircuitBreakerOpenError):
                logger.exception("agent chain trigger failed to spawn the next session for event %s", event.event_id)
