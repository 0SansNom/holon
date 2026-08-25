"""Kafka stream source registry for streaming data ingestion."""

from __future__ import annotations

import json
from typing import Optional

import asyncpg

from holon_common.connector_safety import ConnectorSafetyError, assert_kafka_topic

DDL = """
CREATE TABLE IF NOT EXISTS kafka_stream_source (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    topic TEXT NOT NULL,
    key_field TEXT NOT NULL,
    dataset_name TEXT NOT NULL,
    batch_interval_seconds DOUBLE PRECISION NOT NULL DEFAULT 5.0,
    status TEXT NOT NULL DEFAULT 'active',
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS kafka_stream_state (
    tenant_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    record_key TEXT NOT NULL,
    data JSONB NOT NULL,
    updated_row_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, source_name, record_key)
);
"""


class KafkaStreamConflictError(ValueError):
    pass


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


def _parse_row(row: asyncpg.Record) -> dict:
    return dict(row)


async def register_source(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    topic: str,
    key_field: str,
    dataset_name: str,
    batch_interval_seconds: float,
    created_by_urn: str,
) -> dict:
    try:
        assert_kafka_topic(topic)
    except ConnectorSafetyError as exc:
        raise KafkaStreamConflictError(str(exc)) from exc
    conflicting = await pool.fetchrow(
        """
        SELECT name FROM kafka_stream_source
        WHERE tenant_id = $1 AND dataset_name = $2 AND status = 'active' AND name != $3
        """,
        tenant_id, dataset_name, name,
    )
    if conflicting is not None:
        raise KafkaStreamConflictError(
            f"dataset {dataset_name!r} is already claimed by active stream {conflicting['name']!r}"
        )
    await pool.execute(
        """
        INSERT INTO kafka_stream_source (tenant_id, name, topic, key_field, dataset_name, batch_interval_seconds, created_by_urn)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            topic = EXCLUDED.topic,
            key_field = EXCLUDED.key_field,
            dataset_name = EXCLUDED.dataset_name,
            batch_interval_seconds = EXCLUDED.batch_interval_seconds,
            status = 'active'
        """,
        tenant_id, name, topic, key_field, dataset_name, batch_interval_seconds, created_by_urn,
    )
    return await get_source(pool, tenant_id, name)


async def get_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        "SELECT * FROM kafka_stream_source WHERE tenant_id = $1 AND name = $2", tenant_id, name
    )
    return _parse_row(row) if row else None


async def list_sources(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM kafka_stream_source WHERE tenant_id = $1 ORDER BY name", tenant_id)
    return [_parse_row(row) for row in rows]


async def list_all_active(pool: asyncpg.Pool) -> list[dict]:
    """Every active stream, across tenants — for hydrating consumer
    tasks at startup and for reserving their dataset names against
    plugin/REST-source registration.
    """
    rows = await pool.fetch("SELECT * FROM kafka_stream_source WHERE status = 'active'")
    return [_parse_row(row) for row in rows]


async def set_status(pool: asyncpg.Pool, tenant_id: str, name: str, status: str) -> Optional[dict]:
    await pool.execute(
        "UPDATE kafka_stream_source SET status = $1 WHERE tenant_id = $2 AND name = $3",
        status, tenant_id, name,
    )
    return await get_source(pool, tenant_id, name)


async def delete_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> bool:
    async with pool.acquire() as conn:
        async with conn.transaction():
            deleted = await conn.fetchrow(
                "DELETE FROM kafka_stream_source WHERE tenant_id = $1 AND name = $2 RETURNING name",
                tenant_id, name,
            )
            if deleted is None:
                return False
            await conn.execute(
                "DELETE FROM kafka_stream_state WHERE tenant_id = $1 AND source_name = $2", tenant_id, name
            )
    return True


async def load_state(pool: asyncpg.Pool, tenant_id: str, source_name: str) -> dict[str, dict]:
    rows = await pool.fetch(
        "SELECT record_key, data FROM kafka_stream_state WHERE tenant_id = $1 AND source_name = $2",
        tenant_id, source_name,
    )
    result: dict[str, dict] = {}
    for row in rows:
        data = row["data"]
        result[row["record_key"]] = json.loads(data) if isinstance(data, str) else data
    return result


async def persist_state(pool: asyncpg.Pool, tenant_id: str, source_name: str, records: dict[str, dict]) -> None:
    if not records:
        return
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO kafka_stream_state (tenant_id, source_name, record_key, data)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (tenant_id, source_name, record_key) DO UPDATE SET
                data = EXCLUDED.data, updated_row_at = now()
            """,
            [(tenant_id, source_name, key, json.dumps(value)) for key, value in records.items()],
        )
