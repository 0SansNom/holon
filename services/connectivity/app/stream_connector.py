"""Streaming connector engine — generic over any registered
`kafka_stream_source` (see `kafka_stream_registry.py`). Consumes a
source's topic, keyed by its own `key_field`, maintains full
current-state in Postgres, and commits a periodic Iceberg snapshot of
that state — the same "latest reading per key, not an append-only
event log" shape the original single hardcoded inventory stream always
had, just parameterized instead of compiled in.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable

import asyncpg
from aiokafka import AIOKafkaConsumer

from . import iceberg_writer, kafka_stream_registry

logger = logging.getLogger("connectivity.stream")


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await kafka_stream_registry.ensure_schema(conn)


async def _drain(consumer: AIOKafkaConsumer, *, timeout: float) -> AsyncIterator[Any]:
    """Yield available messages within timeout window."""
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


async def consume_stream_forever(
    *,
    source: dict,
    kafka_bootstrap: str,
    iceberg_config: dict,
    connector_urn: str,
    record_sync: Callable[..., Awaitable[Any]],
    pool: asyncpg.Pool,
) -> None:
    """One consumer per registered stream source — `main.py` spawns and
    cancels one of these per active `kafka_stream_source` row, the same
    enable/disable-without-redeploy lifecycle a connector plugin
    already has.
    """
    tenant_id = source["tenant_id"]
    source_name = source["name"]
    topic = source["topic"]
    key_field = source["key_field"]
    dataset_name = source["dataset_name"]
    batch_interval_seconds = source["batch_interval_seconds"]

    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=kafka_bootstrap,
        group_id=f"connectivity-stream-{tenant_id}-{source_name}",
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await consumer.start()

    latest_by_key = await kafka_stream_registry.load_state(pool, tenant_id, source_name)
    if latest_by_key:
        logger.info("stream %r restored %d records from Postgres", source_name, len(latest_by_key))
    dirty = False
    batch_keys: set[str] = set()
    try:
        while True:
            try:
                async for msg in _drain(consumer, timeout=batch_interval_seconds):
                    payload = msg.value
                    if key_field not in payload:
                        logger.warning(
                            "stream %r: message missing key field %r, skipping: %s", source_name, key_field, payload
                        )
                        continue
                    record_key = str(payload[key_field])
                    latest_by_key[record_key] = {**payload, "id": record_key}
                    batch_keys.add(record_key)
                    dirty = True
            except Exception:
                logger.exception("stream %r consume error, retrying", source_name)

            if dirty and latest_by_key:
                try:
                    changed = {k: latest_by_key[k] for k in batch_keys} if batch_keys else dict(latest_by_key)
                    await kafka_stream_registry.persist_state(pool, tenant_id, source_name, changed)
                    started_at = datetime.now(timezone.utc)
                    rows = list(latest_by_key.values())
                    result = await asyncio.to_thread(
                        iceberg_writer.write_snapshot, rows, dataset_name, **iceberg_config
                    )
                    finished_at = datetime.now(timezone.utc)
                    await record_sync(
                        connector_urn=connector_urn,
                        dataset_name=dataset_name,
                        result=result,
                        started_at=started_at,
                        finished_at=finished_at,
                        tenant_id=tenant_id,
                    )
                    await consumer.commit()
                    dirty = False
                    batch_keys = set()
                    logger.info(
                        "stream %r committed snapshot %s (%d records)", source_name, result.snapshot_id, len(rows)
                    )
                except Exception:
                    logger.exception("stream %r snapshot commit failed, will retry next cycle", source_name)
    finally:
        await consumer.stop()
