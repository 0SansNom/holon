"""Resource tags and featured status registry."""

from __future__ import annotations

import json
from typing import Optional

import asyncpg

DDL = """
CREATE TABLE IF NOT EXISTS resource_tag (
    resource_urn TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    tags JSONB NOT NULL DEFAULT '[]',
    featured BOOLEAN NOT NULL DEFAULT false,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by_urn TEXT NOT NULL,
    PRIMARY KEY (resource_urn, tenant_id)
);
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


def _deserialize(row: asyncpg.Record) -> dict:
    result = dict(row)
    if isinstance(result["tags"], str):
        result["tags"] = json.loads(result["tags"])
    return result


async def get(pool: asyncpg.Pool, *, tenant_id: str, resource_urn: str) -> dict:
    row = await pool.fetchrow(
        "SELECT * FROM resource_tag WHERE tenant_id = $1 AND resource_urn = $2", tenant_id, resource_urn,
    )
    if row is None:
        return {"resource_urn": resource_urn, "tenant_id": tenant_id, "tags": [], "featured": False}
    return _deserialize(row)


async def set_tags(
    pool: asyncpg.Pool, *, tenant_id: str, resource_urn: str, tags: list[str], updated_by_urn: str
) -> dict:
    await pool.execute(
        """
        INSERT INTO resource_tag (resource_urn, tenant_id, tags, updated_by_urn, updated_at)
        VALUES ($1, $2, $3::jsonb, $4, now())
        ON CONFLICT (resource_urn, tenant_id) DO UPDATE SET
            tags = EXCLUDED.tags, updated_by_urn = EXCLUDED.updated_by_urn, updated_at = now()
        """,
        resource_urn, tenant_id, json.dumps(tags), updated_by_urn,
    )
    return await get(pool, tenant_id=tenant_id, resource_urn=resource_urn)


async def set_featured(
    pool: asyncpg.Pool, *, tenant_id: str, resource_urn: str, featured: bool, updated_by_urn: str
) -> dict:
    await pool.execute(
        """
        INSERT INTO resource_tag (resource_urn, tenant_id, featured, updated_by_urn, updated_at)
        VALUES ($1, $2, $3, $4, now())
        ON CONFLICT (resource_urn, tenant_id) DO UPDATE SET
            featured = EXCLUDED.featured, updated_by_urn = EXCLUDED.updated_by_urn, updated_at = now()
        """,
        resource_urn, tenant_id, featured, updated_by_urn,
    )
    return await get(pool, tenant_id=tenant_id, resource_urn=resource_urn)


async def list_matching(
    pool: asyncpg.Pool, *, tenant_id: str, tag: Optional[str] = None, featured: Optional[bool] = None,
) -> list[dict]:
    query = "SELECT * FROM resource_tag WHERE tenant_id = $1"
    params: list = [tenant_id]
    if tag is not None:
        params.append(json.dumps([tag]))
        query += f" AND tags @> ${len(params)}::jsonb"
    if featured is not None:
        params.append(featured)
        query += f" AND featured = ${len(params)}"
    rows = await pool.fetch(query, *params)
    return [_deserialize(row) for row in rows]
