"""Anonymized query log.

The evaluation harness needs real business questions annotated by domain experts.
A query log captures free-text searches to construct query evaluation datasets.

Anonymized deliberately by omission, not redaction: no `principal_urn`,
no actor, no IP — nothing identifying who asked is ever stored, only
*what* was asked and roughly when, at tenant granularity. `/search` is
the place where free-text questions are logged.
"""

from __future__ import annotations

import asyncpg

DDL = """
CREATE TABLE IF NOT EXISTS query_log (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    query_text TEXT NOT NULL,
    result_count INTEGER NOT NULL,
    executed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


async def record_query(pool: asyncpg.Pool, tenant_id: str, query_text: str, result_count: int) -> None:
    await pool.execute(
        "INSERT INTO query_log (tenant_id, query_text, result_count) VALUES ($1, $2, $3)",
        tenant_id,
        query_text,
        result_count,
    )


async def list_recent(pool: asyncpg.Pool, tenant_id: str, limit: int = 100) -> list[dict]:
    rows = await pool.fetch(
        "SELECT id, query_text, result_count, executed_at FROM query_log "
        "WHERE tenant_id = $1 ORDER BY id DESC LIMIT $2",
        tenant_id,
        limit,
    )
    return [dict(row) for row in rows]
