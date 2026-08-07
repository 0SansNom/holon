"""Dead Letter Queue: poison messages are quarantined onto
`platform.dlq.message_quarantined` instead of being silently dropped.
Requires the stack running (`make up`).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import asyncpg
from conftest import TENANT_ID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libs"))

from holon_common import EventConsumer, create_pool  # noqa: E402

KAFKA_BOOTSTRAP = "localhost:19092"  # the OUTSIDE listener — see docker-compose.yml's redpanda service
# Password read from the environment, not hardcoded — CI generates its
# own .env with a different POSTGRES_PASSWORD than a dev's local one
# (see .github/workflows/tests.yml), so a hardcoded value here only ever
# worked by coincidence locally. Default matches .env.example's dev
# convenience value for a plain `pytest tests/` run against `make up`.
CONNECTIVITY_DB_URL = f"postgresql://holon:{os.environ.get('POSTGRES_PASSWORD', 'holon12345')}@localhost:5432/holon_connectivity"


async def _insert_poison_row(marker: str) -> None:
    """A well-formed `EventEnvelope` (passes Pydantic validation) whose
    `event_type` is deliberately never `@register`'d — `relay_forever`'s
    registry check must fail on it and route it to the DLQ, exactly the
    class of poison this queue exists for.
    """
    envelope = {
        "spec_version": "1.0",
        "event_id": uuid.uuid4().hex,
        "event_type": "testprobe.message.injected",  # valid format, deliberately unregistered
        "schema_version": 1,
        "tenant_id": TENANT_ID,
        "workspace_id": "demo",
        "aggregate_type": "TestProbe",
        "aggregate_id": marker,
        "aggregate_version": None,
        "correlation_id": uuid.uuid4().hex,
        "causation_id": None,
        "partition_key": f"{TENANT_ID}/testprobe",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "producer": "test-dlq-probe@0.1.0",
        "actor": {"type": "service_account", "urn": "hl:acme:global:service-account:test-probe", "on_behalf_of": None},
        "classification": "internal",
        "payload": {"marker": marker},
    }
    pool = await create_pool(CONNECTIVITY_DB_URL)
    try:
        await pool.execute(
            "INSERT INTO event_outbox (event_id, envelope) VALUES ($1, $2::jsonb)",
            envelope["event_id"],
            json.dumps(envelope),
        )
    finally:
        await pool.close()


async def _find_dlq_message(marker: str, timeout: float = 30.0) -> Optional[dict]:
    consumer = EventConsumer(KAFKA_BOOTSTRAP, topics=["platform"], group_id=f"dlq-test-{uuid.uuid4().hex}")
    await consumer.start()
    try:
        iterator = consumer.__aiter__()
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return None
            try:
                event = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
            except (asyncio.TimeoutError, StopAsyncIteration):
                return None
            if (
                event.event_type == "platform.dlq.message_quarantined"
                and event.payload.get("raw_payload", {}).get("marker") == marker
            ):
                return event.payload
    finally:
        await consumer.stop()


def test_poison_outbox_row_is_quarantined_to_the_dlq() -> None:
    marker = uuid.uuid4().hex
    asyncio.run(_insert_poison_row(marker))

    payload = asyncio.run(_find_dlq_message(marker))
    assert payload is not None, "expected message never appeared on platform.dlq.message_quarantined"
    assert payload["original_event_type"] == "testprobe.message.injected", payload
    assert "no payload schema registered" in payload["error"], payload
