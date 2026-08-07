"""RelationType registry. Unlike ObjectType, a workspace admin can
register a new RelationType at runtime directly (no draft step —
cardinality/endpoint validation is enforced synchronously here), but
only as a *definition*: a newly-registered relation type is not wired
into any traversal endpoint, the seeded ones (`object_types.RELATION_TYPES`)
are still the ones `routers/objects.py` knows how to traverse.
"""

from __future__ import annotations

import asyncpg

from .object_types import get_object_type
from .urns import object_type_urn, relation_type_urn

VALID_CARDINALITIES = {"one_to_one", "one_to_many", "many_to_one", "many_to_many"}


async def get_relation_type(pool: asyncpg.Pool, urn: str) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM relation_type WHERE urn = $1", urn)
    return dict(row) if row else None


async def list_relation_types(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM relation_type WHERE tenant_id = $1 ORDER BY name", tenant_id)
    return [dict(row) for row in rows]


async def create_relation_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    source_object_type: str,
    target_object_type: str,
    source_property: str,
    cardinality: str,
) -> dict:
    """Explicit cardinality and direction with existing endpoints
    enforced here rather than merely true by
    construction of the hardcoded `RELATION_TYPES` seed list. Definition
    only: this does not wire the new relation into any traversal endpoint
    (the three existing ones in `routers/objects.py` stay hand-written).
    """
    if cardinality not in VALID_CARDINALITIES:
        raise ValueError(f"invalid cardinality: {cardinality!r} (must be one of {sorted(VALID_CARDINALITIES)})")

    source_urn = object_type_urn(tenant_id, workspace_id, source_object_type)
    if await get_object_type(pool, source_urn) is None:
        raise ValueError(f"source_object_type does not exist: {source_object_type}")

    target_urn = object_type_urn(tenant_id, workspace_id, target_object_type)
    if await get_object_type(pool, target_urn) is None:
        raise ValueError(f"target_object_type does not exist: {target_object_type}")

    urn = relation_type_urn(tenant_id, workspace_id, name)
    await pool.execute(
        """
        INSERT INTO relation_type (urn, tenant_id, name, source_object_type_urn, target_object_type_urn, source_property, cardinality)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        urn,
        tenant_id,
        name,
        source_urn,
        target_urn,
        source_property,
        cardinality,
    )
    return await get_relation_type(pool, urn)
