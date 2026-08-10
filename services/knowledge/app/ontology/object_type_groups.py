"""Object Type Group registry — a named, navigational cluster of
ObjectTypes (Foundry's Ontology Manager concept of the same name). Not a
new permission or schema layer, just a validated list: every member name
must resolve to a real ObjectType, the same check `relation_types.py`
already does for its source/target endpoints, and for the identical
reason — it works uniformly for a seeded or a self-serve ObjectType,
since both have a real row in the `object_type` table.
"""

from __future__ import annotations

import json

import asyncpg

from .object_types import get_object_type
from .urns import object_type_urn


async def create_object_type_group(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    description: str,
    object_types: list[str],
) -> dict:
    for ot_name in object_types:
        urn = object_type_urn(tenant_id, workspace_id, ot_name)
        if await get_object_type(pool, urn) is None:
            raise ValueError(f"unknown ObjectType: {ot_name}")

    await pool.execute(
        """
        INSERT INTO object_type_group (tenant_id, name, description, object_types)
        VALUES ($1, $2, $3, $4::jsonb)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            description = EXCLUDED.description,
            object_types = EXCLUDED.object_types
        """,
        tenant_id, name, description, json.dumps(object_types),
    )
    return await get_object_type_group(pool, tenant_id, name)


def _parse_row(row: asyncpg.Record) -> dict:
    result = dict(row)
    if isinstance(result.get("object_types"), str):
        result["object_types"] = json.loads(result["object_types"])
    return result


async def get_object_type_group(pool: asyncpg.Pool, tenant_id: str, name: str) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM object_type_group WHERE tenant_id = $1 AND name = $2", tenant_id, name)
    return _parse_row(row) if row else None


async def list_object_type_groups(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM object_type_group WHERE tenant_id = $1 ORDER BY name", tenant_id)
    return [_parse_row(row) for row in rows]
