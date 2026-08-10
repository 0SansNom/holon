"""Shared Property Type registry — a canonical, reusable *property*
definition (API name + display name + description), wrapping a
`value_type.py` Value Type for its underlying data-type constraint.

The distinction from a Value Type: a Value Type is just the data shape
("Email" = string + a regex) and can be referenced by many differently-
named properties across many ObjectTypes with no relationship between
them. A Shared Property Type is the *property* itself, named once
(`api_name`), meant to be referenced identically by that same
conceptual property everywhere it appears — renaming/redescribing it
here is a single edit, not one per ObjectType. Referenced from
`object_types.py`'s `property_types` via a third leaf kind,
`{"kind": "shared_property_type", "shared_property_type": "email"}`,
alongside the existing `value_type`/`struct`/`array` kinds (see
`publishing.py`'s `_validate_property_types`), and from
`libs/holon_osdk` for codegen (resolves through to the wrapped Value
Type's `base_type`, and surfaces `display_name`/`description` as a
generated doc-comment).
"""

from __future__ import annotations

from typing import Optional

import asyncpg

from . import value_types as value_types_module


async def create_shared_property_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    api_name: str,
    display_name: str,
    value_type: str,
    description: str = "",
) -> dict:
    if await value_types_module.get_value_type(pool, tenant_id, value_type) is None:
        raise ValueError(f"unknown value_type: {value_type!r}")

    await pool.execute(
        """
        INSERT INTO shared_property_type (tenant_id, api_name, display_name, value_type, description)
        VALUES ($1, $2, $3, $4, $5)
        """,
        tenant_id, api_name, display_name, value_type, description,
    )
    return await get_shared_property_type(pool, tenant_id, api_name)


async def update_shared_property_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    api_name: str,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Partial update — `value_type` and `api_name` are deliberately not
    accepted params: changing the wrapped Value Type would silently
    change the data contract for every property referencing this Shared
    Property Type by name. `None` means "leave unchanged".
    """
    current = await get_shared_property_type(pool, tenant_id, api_name)
    if current is None:
        raise ValueError(f"unknown shared property type: {api_name!r}")

    new_display_name = current["display_name"] if display_name is None else display_name
    new_description = current["description"] if description is None else description

    await pool.execute(
        "UPDATE shared_property_type SET display_name = $1, description = $2 WHERE tenant_id = $3 AND api_name = $4",
        new_display_name, new_description, tenant_id, api_name,
    )
    return await get_shared_property_type(pool, tenant_id, api_name)


async def get_shared_property_type(pool: asyncpg.Pool, tenant_id: str, api_name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        "SELECT * FROM shared_property_type WHERE tenant_id = $1 AND api_name = $2", tenant_id, api_name
    )
    return dict(row) if row else None


async def list_shared_property_types(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM shared_property_type WHERE tenant_id = $1 ORDER BY api_name", tenant_id)
    return [dict(row) for row in rows]
