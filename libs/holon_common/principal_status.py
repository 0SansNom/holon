"""Process-local JWT denylist fed by Identity events and boot snapshot.

The denylist is in-memory, so every replica must receive every event.
`principal_status_group_id` is unique per process so Kafka does not
share a consumer group across replicas (which would deliver each
message to only one pod).

Kafka `auto_offset_reset=latest` means a restarted pod would miss past
disable/revoke events. `hydrate_revocation_snapshot` pulls the current
sets from Identity before the process serves traffic.
"""

from __future__ import annotations

import logging
import os
import socket
from typing import Any, Optional

from .auth import (
    Principal,
    active_jwt,
    apply_principal_status_payload,
    apply_token_revoked_payload,
    issue_token,
    replace_disabled_principal_urns,
    replace_revoked_jtis,
)
from .events import EventConsumer, EventEnvelope
from .urn import build as build_urn

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
    """Apply principal disable/enable and token revocation; optionally invalidate a PermissionClient cache."""
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
    if event.event_type == "identity.token.revoked":
        apply_token_revoked_payload(event.payload)
        return
    if event.event_type in {"identity.permission.granted", "identity.permission.revoked"} and authz is not None:
        authz.invalidate_principal(event.payload["principal_urn"])


def _hydrate_bearer(*, tenant_id: str) -> Optional[str]:
    raw = os.environ.get("HOLON_MINTABLE_PRINCIPAL_URNS")
    if raw is None:
        return None
    local = next((p.strip() for p in raw.split(",") if p.strip()), None)
    if not local:
        return None
    urn = local if local.startswith("hl:") else build_urn(tenant_id, "global", "service-account", local)
    principal = Principal(
        urn=urn,
        type="service_account",
        tenant_id=tenant_id,
        display_name="revocation-hydrate",
    )
    secret, kid, secrets = active_jwt()
    return issue_token(principal, secret, ttl_seconds=120, kid=kid, secrets=secrets)


async def hydrate_revocation_snapshot(*, identity_url: Optional[str] = None) -> None:
    """Replace in-memory denylists from Identity's durable snapshot.

    No-op (with a warning) when this process cannot mint an SA JWT or
    has no Identity URL — Identity itself loads from Postgres instead.
    """
    url = (identity_url or os.environ.get("HOLON_IDENTITY_URL") or "").rstrip("/")
    if not url:
        logger.warning("HOLON_IDENTITY_URL unset — skipping revocation snapshot hydrate")
        return
    tenant_id = os.environ.get("HOLON_TENANT_ID") or ""
    if not tenant_id:
        logger.warning("HOLON_TENANT_ID unset — skipping revocation snapshot hydrate")
        return
    bearer = _hydrate_bearer(tenant_id=tenant_id)
    if not bearer:
        logger.warning("cannot mint SA JWT for revocation snapshot hydrate")
        return
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{url}/internal/revocation-snapshot",
            headers={"Authorization": f"Bearer {bearer}"},
        )
        response.raise_for_status()
        data = response.json()
    replace_disabled_principal_urns(data.get("disabled_principal_urns") or [])
    replace_revoked_jtis(data.get("revoked_jtis") or [])
    logger.info(
        "hydrated revocation snapshot (%s disabled principals, %s revoked jtis)",
        len(data.get("disabled_principal_urns") or []),
        len(data.get("revoked_jtis") or []),
    )
