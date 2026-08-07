"""Interface registry — polymorphism across ObjectTypes: a named,
checked contract (required properties/actions), not just a label.
Publish-time validation of `implements` against these lives in
`publishing.py` (`_validate_implements`), not here — this module only
owns the interface *registry* itself.
"""

from __future__ import annotations

import json
from typing import Optional

import asyncpg


async def create_interface_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    required_properties: list[str],
    required_actions: list[str],
    description: str = "",
) -> dict:
    await pool.execute(
        """
        INSERT INTO interface_type (tenant_id, name, required_properties, required_actions, description)
        VALUES ($1, $2, $3::jsonb, $4::jsonb, $5)
        """,
        tenant_id, name, json.dumps(required_properties), json.dumps(required_actions), description,
    )
    return await get_interface_type(pool, tenant_id, name)


def _parse_interface_row(row: asyncpg.Record) -> dict:
    result = dict(row)
    for key in ("required_properties", "required_actions"):
        if isinstance(result[key], str):
            result[key] = json.loads(result[key])
    return result


async def get_interface_type(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM interface_type WHERE tenant_id = $1 AND name = $2", tenant_id, name)
    return _parse_interface_row(row) if row else None


async def list_interface_types(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM interface_type WHERE tenant_id = $1 ORDER BY name", tenant_id)
    return [_parse_interface_row(row) for row in rows]
