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


async def update_interface_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    required_properties: Optional[list[str]] = None,
    required_actions: Optional[list[str]] = None,
    description: Optional[str] = None,
) -> dict:
    """Partial update — `name` is deliberately not an accepted param: it's
    the key referenced from every ObjectType's `implements` list.
    `None` means "leave unchanged".
    """
    current = await get_interface_type(pool, tenant_id, name)
    if current is None:
        raise ValueError(f"unknown interface: {name!r}")

    new_required_properties = current["required_properties"] if required_properties is None else required_properties
    new_required_actions = current["required_actions"] if required_actions is None else required_actions
    new_description = current["description"] if description is None else description

    await pool.execute(
        """
        UPDATE interface_type SET required_properties = $1::jsonb, required_actions = $2::jsonb, description = $3
        WHERE tenant_id = $4 AND name = $5
        """,
        json.dumps(new_required_properties), json.dumps(new_required_actions), new_description, tenant_id, name,
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
