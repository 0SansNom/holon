"""RelationType registry. Unlike ObjectType, a workspace admin can
register a new RelationType at runtime directly (no draft step —
cardinality/endpoint validation is enforced synchronously here), but
only as a *definition*: a newly-registered relation type is not wired
into any traversal endpoint, the seeded ones (`object_types.RELATION_TYPES`)
are still the ones `routers/objects.py` knows how to traverse.
"""

from __future__ import annotations

from typing import Optional

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
    target_property: str,
    cardinality: str,
) -> dict:
    """Explicit cardinality and direction with existing endpoints
    enforced here rather than merely true by construction of the
    hardcoded `RELATION_TYPES` seed list. `target_property` names the
    reverse-direction accessor (e.g. `Order.customer`'s `orders`) — a
    real Link Type names both ends, not just the forward one; validated
    here the same trust tier `source_property` already has (non-empty,
    not deep-checked against the target's real properties). Definition
    only beyond that: registering a relation doesn't wire it into
    anything by itself — real traversal (`routers/objects/seeded.py`'s
    instance graph and `/links/{link_name}` endpoint) reads this
    registry live at request time instead.
    """
    if cardinality not in VALID_CARDINALITIES:
        raise ValueError(f"invalid cardinality: {cardinality!r} (must be one of {sorted(VALID_CARDINALITIES)})")
    if not target_property:
        raise ValueError("target_property is required — the reverse-direction accessor name")

    source_urn = object_type_urn(tenant_id, workspace_id, source_object_type)
    if await get_object_type(pool, source_urn) is None:
        raise ValueError(f"source_object_type does not exist: {source_object_type}")

    target_urn = object_type_urn(tenant_id, workspace_id, target_object_type)
    if await get_object_type(pool, target_urn) is None:
        raise ValueError(f"target_object_type does not exist: {target_object_type}")

    urn = relation_type_urn(tenant_id, workspace_id, name)
    await pool.execute(
        """
        INSERT INTO relation_type (urn, tenant_id, name, source_object_type_urn, target_object_type_urn, source_property, target_property, cardinality)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        urn,
        tenant_id,
        name,
        source_urn,
        target_urn,
        source_property,
        target_property,
        cardinality,
    )
    return await get_relation_type(pool, urn)


async def update_relation_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    target_property: Optional[str] = None,
    cardinality: Optional[str] = None,
) -> dict:
    """Partial update — source/target ObjectType and `source_property`
    are deliberately not accepted params: they're the structural
    identity of the link. Only the reverse-direction accessor name and
    the declared cardinality are safe to adjust without breaking
    anything that already resolved this relation. `None` means "leave
    unchanged".
    """
    urn = relation_type_urn(tenant_id, workspace_id, name)
    current = await get_relation_type(pool, urn)
    if current is None:
        raise ValueError(f"unknown RelationType: {name!r}")

    new_target_property = current["target_property"] if target_property is None else target_property
    new_cardinality = current["cardinality"] if cardinality is None else cardinality
    if not new_target_property:
        raise ValueError("target_property is required — the reverse-direction accessor name")
    if new_cardinality not in VALID_CARDINALITIES:
        raise ValueError(f"invalid cardinality: {new_cardinality!r} (must be one of {sorted(VALID_CARDINALITIES)})")

    await pool.execute(
        "UPDATE relation_type SET target_property = $1, cardinality = $2 WHERE urn = $3",
        new_target_property, new_cardinality, urn,
    )
    return await get_relation_type(pool, urn)
