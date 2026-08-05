"""Streaming connector — streaming Connectivity capability.

Unlike every other connector, this one isn't triggered by `/sync`: a
long-running background task (started in `main.py`'s `lifespan`,
mirroring the outbox relay task already running there) continuously
consumes an *external* system's raw Kafka topic via a plain
`aiokafka.AIOKafkaConsumer` — not `holon_common.EventConsumer`, which
assumes Holon's own `EventEnvelope` shape; this topic carries an external
system's native messages, the same reasoning that makes
`mongo_connector`/`rest_connector` use their source's own client rather
than an internal abstraction.

Ingestion shape: accumulate the latest reading per SKU in memory (a
single-partition topic makes "last message wins" correct — the same
reasoning `tests/test_projection_rebuild.py` already relies on), and
periodically — not per message — commit one new Iceberg snapshot and
announce it via the *same* `connectivity.sync.completed` event every
other connector uses (`record_sync`, injected by `main.py` so this module
never needs its own DB/outbox wiring). Everything downstream
(cataloguing, serving store, search, execution) needs zero changes: only
the ingestion trigger is new, not the pipeline. Deliberately a simple
micro-batch, not a true per-event exactly-once system — stated honestly,
a further step if this build ever needs it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable

from aiokafka import AIOKafkaConsumer

from . import iceberg_writer

logger = logging.getLogger("connectivity.stream")

EXTERNAL_TOPIC = "external-inventory-stream"
BATCH_INTERVAL_SECONDS = 5.0


async def _drain(consumer: AIOKafkaConsumer, *, timeout: float) -> AsyncIterator[Any]:
    """Yield whatever messages are already available within `timeout`
    seconds, then return — a bounded micro-batch window, not an unbounded
    per-message loop (which would never leave room to commit a snapshot).
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return
        try:
            msg = await asyncio.wait_for(consumer.getone(), timeout=remaining)
        except asyncio.TimeoutError:
            return
        yield msg


async def consume_inventory_stream_forever(
    *,
    kafka_bootstrap: str,
    iceberg_config: dict,
    connector_urn: str,
    record_sync: Callable[..., Awaitable[Any]],
) -> None:
    consumer = AIOKafkaConsumer(
        EXTERNAL_TOPIC,
        bootstrap_servers=kafka_bootstrap,
        group_id="connectivity-inventory-stream",
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )
    await consumer.start()

    latest_by_sku: dict[str, dict] = {}
    dirty = False
    try:
        while True:
            try:
                async for msg in _drain(consumer, timeout=BATCH_INTERVAL_SECONDS):
                    payload = msg.value
                    latest_by_sku[payload["sku"]] = {
                        "id": payload["sku"],
                        "warehouse": payload["warehouse"],
                        "quantity": payload["quantity"],
                        "updated_at": payload["updated_at"],
                    }
                    dirty = True
            except Exception:
                logger.exception("inventory stream consume error, retrying")

            if dirty and latest_by_sku:
                try:
                    started_at = datetime.now(timezone.utc)
                    rows = list(latest_by_sku.values())
                    result = await asyncio.to_thread(
                        iceberg_writer.write_snapshot, rows, "inventory_levels", **iceberg_config
                    )
                    finished_at = datetime.now(timezone.utc)
                    await record_sync(
                        connector_urn=connector_urn,
                        dataset_name="inventory_levels",
                        result=result,
                        started_at=started_at,
                        finished_at=finished_at,
                    )
                    dirty = False
                    logger.info(
                        "inventory stream committed snapshot %s (%d SKUs)", result.snapshot_id, len(rows)
                    )
                except Exception:
                    logger.exception("inventory stream snapshot commit failed, will retry next cycle")
    finally:
        await consumer.stop()
