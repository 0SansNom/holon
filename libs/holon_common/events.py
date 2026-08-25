"""Platform Event Bus — event envelope and Kafka wrappers.

Backing implementation: Redpanda locally (Kafka wire-protocol compatible), via aiokafka.
Application code only ever talks to this interface.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError
from pydantic import BaseModel, Field, field_validator

from . import registry
from .observability import retry_with_backoff
from .urn import build as build_urn

logger = logging.getLogger("holon_common.events")


async def _start_with_retry(
    startable: Any, *, what: str, attempts: int = 15, delay: float = 2.0
) -> None:
    """Kafka bootstrap retry using `retry_with_backoff`.
    """
    await retry_with_backoff(
        startable.start,
        attempts=attempts,
        base_delay=delay,
        max_delay=delay,
        retry_on=(KafkaConnectionError,),
        what=what,
    )

_EVENT_TYPE_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*(\.[a-z0-9]+(_[a-z0-9]+)*){2}$")


class EventActor(BaseModel):
    type: str  # user | service_account | agent
    urn: str
    on_behalf_of: Optional[str] = None


class EventEnvelope(BaseModel):
    """Naming `{context}.{aggregate}.{fact_in_past_tense}`. An event
    describes a fact that already happened, never an intent or a command.
    """

    spec_version: str = "1.0"
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    event_type: str
    schema_version: int = 1

    tenant_id: str
    workspace_id: Optional[str] = None

    aggregate_type: str
    aggregate_id: str
    aggregate_version: Optional[int] = None

    correlation_id: str
    causation_id: Optional[str] = None
    partition_key: str

    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    producer: str
    actor: EventActor

    classification: str = "internal"
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type")
    @classmethod
    def _event_type_format(cls, v: str) -> str:
        if not _EVENT_TYPE_RE.match(v):
            raise ValueError(f"event_type must follow {{context}}.{{aggregate}}.{{fact_in_past_tense}}: {v!r}")
        return v

    def topic(self) -> str:
        return self.event_type.split(".", 1)[0]


class EventProducer:
    def __init__(self, bootstrap_servers: str):
        self._bootstrap_servers = bootstrap_servers
        self._producer: Optional[AIOKafkaProducer] = None

    async def start(self) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k is not None else None,
        )
        await _start_with_retry(self._producer, what="EventProducer")

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()

    async def publish(self, envelope: EventEnvelope) -> None:
        if self._producer is None:
            raise RuntimeError("EventProducer.start() must be called before publish()")
        registry.validate(envelope.event_type, envelope.schema_version, envelope.payload)
        await self._producer.send_and_wait(
            envelope.topic(),
            value=envelope.model_dump(mode="json"),
            key=envelope.partition_key,
        )


def make_dlq_envelope(*, original_topic: str, original_event_type: str, tenant_id: str, error: str, raw_payload: dict) -> EventEnvelope:
    """Dead Letter Queue helper — shared by `EventConsumer._quarantine`
    (a poison message off the bus) and `outbox.relay_forever` (a poison
    row that will never successfully publish).
    """
    event_id = uuid.uuid4().hex
    return EventEnvelope(
        event_id=event_id,
        event_type="platform.dlq.message_quarantined",
        tenant_id=tenant_id,
        aggregate_type="DeadLetterQueue",
        aggregate_id=event_id,
        correlation_id=event_id,
        partition_key=f"{tenant_id}/dlq",
        producer="holon_common.events",
        actor=EventActor(type="service_account", urn=build_urn(tenant_id, "global", "service-account", "platform-dlq")),
        payload={
            "original_topic": original_topic,
            "original_event_type": original_event_type,
            "error": error,
            "raw_payload": raw_payload,
        },
    )


class EventConsumer:
    """The consumer MUST be idempotent; offsets are committed only
    after successful processing (no auto-commit).
    """

    def __init__(
        self,
        bootstrap_servers: str,
        topics: list[str],
        group_id: str,
        *,
        dlq_producer: Optional["EventProducer"] = None,
        auto_offset_reset: str = "earliest",
    ):
        self._bootstrap_servers = bootstrap_servers
        self._topics = topics
        self._group_id = group_id
        self._consumer: Optional[AIOKafkaConsumer] = None
        self._dlq_producer = dlq_producer
        self._auto_offset_reset = auto_offset_reset

    async def start(self) -> None:
        self._consumer = AIOKafkaConsumer(
            *self._topics,
            bootstrap_servers=self._bootstrap_servers,
            group_id=self._group_id,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset=self._auto_offset_reset,
            enable_auto_commit=False,
        )
        await _start_with_retry(self._consumer, what="EventConsumer")

    async def stop(self) -> None:
        if self._consumer is not None:
            await self._consumer.stop()

    async def __aiter__(self) -> AsyncIterator[EventEnvelope]:
        if self._consumer is None:
            raise RuntimeError("EventConsumer.start() must be called before iterating")
        async for msg in self._consumer:
            try:
                envelope = EventEnvelope.model_validate(msg.value)
                registry.validate(envelope.event_type, envelope.schema_version, envelope.payload)
            except Exception as exc:  # noqa: BLE001 — a poison message must not kill the consumer loop
                logger.exception("skipping message: failed envelope/registry validation")
                await self._quarantine(msg.value, exc)
                continue
            yield envelope

    async def _quarantine(self, raw_value: Any, exc: Exception) -> None:
        """Dead Letter Queue: a poison message is skipped from normal processing
        and lands somewhere inspectable.
        """
        if self._dlq_producer is None:
            return
        original_event_type = raw_value.get("event_type", "unknown") if isinstance(raw_value, dict) else "unknown"
        tenant_id = raw_value.get("tenant_id", "unknown") if isinstance(raw_value, dict) else "unknown"
        dlq_event = make_dlq_envelope(
            original_topic=",".join(self._topics),
            original_event_type=original_event_type,
            tenant_id=tenant_id,
            error=str(exc),
            raw_payload=raw_value if isinstance(raw_value, dict) else {"_unparseable": str(raw_value)},
        )
        try:
            await self._dlq_producer.publish(dlq_event)
        except Exception:
            logger.exception("failed to publish quarantined message to DLQ — original message still dropped")

    async def commit(self) -> None:
        if self._consumer is not None:
            await self._consumer.commit()
