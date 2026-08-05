"""Transactional outbox.

"A service that mutates its own state and publishes an event MUST write
both in the same local transaction, via an outbox table relayed to the
bus. Writing directly to both the database and the bus is forbidden."

Expected usage in a service:

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("INSERT INTO dataset (...) VALUES (...)")
            await outbox.enqueue(conn, envelope)

    # one background task per process:
    asyncio.create_task(outbox.relay_forever(pool, producer))
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg
from pydantic import ValidationError

from typing import Optional

from . import registry
from .events import EventEnvelope, EventProducer, make_dlq_envelope

logger = logging.getLogger("holon_common.outbox")

_DDL = """
CREATE TABLE IF NOT EXISTS event_outbox (
    id BIGSERIAL PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    envelope JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ
);
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)


async def enqueue(conn: asyncpg.Connection, envelope: EventEnvelope) -> None:
    """MUST be called within the same transaction as the domain write."""
    await conn.execute(
        "INSERT INTO event_outbox (event_id, envelope) VALUES ($1, $2::jsonb)",
        envelope.event_id,
        envelope.model_dump_json(),
    )


async def relay_forever(
    pool: asyncpg.Pool,
    producer: EventProducer,
    poll_interval: float = 1.0,
    *,
    dlq_producer: Optional[EventProducer] = None,
) -> None:
    """Relays outbox rows to the bus.

    Only a registry-validation failure (a genuinely poison row) is skipped
    in place, matching `catalog.consume_events`'s poison-message handling
    (`published_at` set anyway, and — if `dlq_producer` is given — also
    quarantined onto the real Dead Letter Queue instead of only a
    log line). A broker/network error from `producer.publish`'s actual
    send is a *different* failure class — transient, not deterministic —
    and must propagate to the outer retry loop instead.
    so much as hiccups, which this host has done more than once this
    session. Validated explicitly here, before the send, specifically so
    the two failure classes can be told apart.
    """
    while True:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, envelope FROM event_outbox WHERE published_at IS NULL "
                    "ORDER BY id ASC LIMIT 50"
                )
                for row in rows:
                    envelope = EventEnvelope.model_validate_json(row["envelope"])
                    try:
                        registry.validate(envelope.event_type, envelope.schema_version, envelope.payload)
                    except (registry.UnknownEventTypeError, registry.UnknownSchemaVersionError, ValidationError) as exc:
                        logger.exception("poison outbox row %s failed registry validation, skipping", row["id"])
                        if dlq_producer is not None:
                            try:
                                await dlq_producer.publish(
                                    make_dlq_envelope(
                                        original_topic=envelope.topic(),
                                        original_event_type=envelope.event_type,
                                        tenant_id=envelope.tenant_id,
                                        error=str(exc),
                                        raw_payload=envelope.payload,
                                    )
                                )
                            except Exception:
                                logger.exception("failed to publish quarantined outbox row %s to DLQ", row["id"])
                        await conn.execute(
                            "UPDATE event_outbox SET published_at = now() WHERE id = $1", row["id"]
                        )
                        continue
                    await producer.publish(envelope)  # broker/network errors propagate — see docstring
                    await conn.execute(
                        "UPDATE event_outbox SET published_at = now() WHERE id = $1", row["id"]
                    )
        except Exception:  # noqa: BLE001 — the relay must never die silently
            logger.exception("outbox relay error, retrying in %ss", poll_interval)
        await asyncio.sleep(poll_interval)
