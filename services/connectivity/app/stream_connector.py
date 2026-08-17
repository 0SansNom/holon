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

Ingestion shape: keep the latest reading per SKU in Postgres
(`stream_inventory_state`) and mirror it in memory for the batch window.
Kafka offsets are committed **only after** a successful Iceberg snapshot
+ outbox finalize — auto-commit would advance past messages that never
landed. On process restart, SKU state is reloaded from Postgres so the
next snapshot is still a full last-write-wins map; uncommitted Kafka
messages redeliver and merge. Periodic micro-batches commit one Iceberg
snapshot and announce it via the same `connectivity.sync.completed`
event every other connector uses.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable

import asyncpg
from aiokafka import AIOKafkaConsumer

from . import iceberg_writer

logger = logging.getLogger("connectivity.stream")

EXTERNAL_TOPIC = "external-inventory-stream"
BATCH_INTERVAL_SECONDS = 5.0

_STATE_DDL = """
CREATE TABLE IF NOT EXISTS stream_inventory_state (
    sku TEXT PRIMARY KEY,
    warehouse TEXT NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    updated_at TEXT NOT NULL,
    updated_row_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(_STATE_DDL)


async def _load_state(pool: asyncpg.Pool) -> dict[str, dict]:
    rows = await pool.fetch(
        "SELECT sku, warehouse, quantity, updated_at FROM stream_inventory_state"
    )
    return {
        row["sku"]: {
            "id": row["sku"],
            "warehouse": row["warehouse"],
            "quantity": row["quantity"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    }


async def _persist_state(pool: asyncpg.Pool, rows: list[dict]) -> None:
    if not rows:
        return
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO stream_inventory_state (sku, warehouse, quantity, updated_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (sku) DO UPDATE SET
                warehouse = EXCLUDED.warehouse,
                quantity = EXCLUDED.quantity,
                updated_at = EXCLUDED.updated_at,
                updated_row_at = now()
            """,
            [(r["id"], r["warehouse"], r["quantity"], r["updated_at"]) for r in rows],
        )


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
    pool: asyncpg.Pool,
) -> None:
    consumer = AIOKafkaConsumer(
        EXTERNAL_TOPIC,
        bootstrap_servers=kafka_bootstrap,
        group_id="connectivity-inventory-stream",
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await consumer.start()

    latest_by_sku = await _load_state(pool)
    if latest_by_sku:
        logger.info("inventory stream restored %d SKUs from Postgres", len(latest_by_sku))
    dirty = False
    batch_skus: set[str] = set()
    try:
        while True:
            try:
                async for msg in _drain(consumer, timeout=BATCH_INTERVAL_SECONDS):
                    payload = msg.value
                    sku = payload["sku"]
                    latest_by_sku[sku] = {
                        "id": sku,
                        "warehouse": payload["warehouse"],
                        "quantity": payload["quantity"],
                        "updated_at": payload["updated_at"],
                    }
                    batch_skus.add(sku)
                    dirty = True
            except Exception:
                logger.exception("inventory stream consume error, retrying")

            if dirty and latest_by_sku:
                try:
                    changed = [latest_by_sku[s] for s in batch_skus] if batch_skus else list(latest_by_sku.values())
                    # Durability before Iceberg: crash after this still has
                    # SKU state in Postgres; Kafka offsets are not yet
                    # committed, so messages redeliver (SKU last-write-wins).
                    await _persist_state(pool, changed)
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
                    await consumer.commit()
                    dirty = False
                    batch_skus = set()
                    logger.info(
                        "inventory stream committed snapshot %s (%d SKUs)", result.snapshot_id, len(rows)
                    )
                except Exception:
                    logger.exception("inventory stream snapshot commit failed, will retry next cycle")
    finally:
        await consumer.stop()
