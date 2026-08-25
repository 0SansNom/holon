"""Process-local JWT denylist fed by `identity.principal.status_changed`.

The denylist is in-memory, so every replica must receive every event.
`principal_status_group_id` is unique per process so Kafka does not
share a consumer group across replicas (which would deliver each
message to only one pod).
"""

from __future__ import annotations

import logging
import os
import socket
from typing import Any, Optional

from .auth import apply_principal_status_payload
from .events import EventConsumer, EventEnvelope

logger = logging.getLogger("holon_common.principal_status")


def principal_status_group_id(service_name: str) -> str:
    return f"{service_name}-principal-status-{socket.gethostname()}-{os.getpid()}"


def make_principal_status_consumer(
    bootstrap_servers: str,
    *,
    service_name: str,
    dlq_producer: Any = None,
) -> EventConsumer:
    return EventConsumer(
        bootstrap_servers,
        topics=["identity"],
        group_id=principal_status_group_id(service_name),
        dlq_producer=dlq_producer,
        auto_offset_reset="latest",
    )


async def consume_identity_auth_events(consumer: EventConsumer, *, authz: Optional[Any] = None) -> None:
    """Apply principal disable/enable; optionally invalidate a PermissionClient cache."""
    await consumer.start()
    async for event in consumer:
        try:
            _apply_identity_auth_event(event, authz=authz)
        except Exception:
            logger.exception("failed to process identity event %s", event.event_id)
        await consumer.commit()


def _apply_identity_auth_event(event: EventEnvelope, *, authz: Optional[Any]) -> None:
    if event.event_type == "identity.principal.status_changed":
        apply_principal_status_payload(event.payload)
        urn = event.payload.get("principal_urn") or ""
        if authz is not None and urn:
            authz.invalidate_principal(urn)
        return
    if event.event_type in {"identity.permission.granted", "identity.permission.revoked"} and authz is not None:
        authz.invalidate_principal(event.payload["principal_urn"])
