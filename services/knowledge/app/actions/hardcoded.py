"""Shared Action event envelope helper."""

from __future__ import annotations

import uuid

from holon_common import EventActor, EventEnvelope, Principal

WORKFLOW_ENGINE_URN_NAME = "automation-workflow-engine"


def _event(*, event_type: str, tenant_id: str, workspace_id: str, instance_urn: str, actor: Principal, payload: dict) -> EventEnvelope:
    event_id = uuid.uuid4().hex
    return EventEnvelope(
        event_id=event_id,
        event_type=event_type,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        aggregate_type="ObjectType",
        aggregate_id=instance_urn,
        correlation_id=event_id,
        partition_key=f"{tenant_id}/{instance_urn}",
        producer="knowledge-platform@0.1.0",
        actor=EventActor(type=actor.type, urn=actor.urn, on_behalf_of=actor.on_behalf_of),
        payload=payload,
    )
