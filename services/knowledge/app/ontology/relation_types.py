"""RelationType registry — FK, join-dataset (M:N), and object-backed links.

Unlike ObjectType, a workspace admin can register a RelationType at runtime
directly (no draft step); cardinality/endpoint/storage validation is
enforced synchronously here. Traversal reads this registry live.
"""

from __future__ import annotations

from typing import Optional

import asyncpg

from .object_types import get_object_type
from .urns import object_type_urn, relation_type_urn

VALID_CARDINALITIES = {"one_to_one", "one_to_many", "many_to_one", "many_to_many"}
VALID_STORAGE_KINDS = {"foreign_key", "join_dataset", "object_backed"}


async def get_relation_type(pool: asyncpg.Pool, urn: str) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM relation_type WHERE urn = $1", urn)
    return dict(row) if row else None


async def list_relation_types(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM relation_type WHERE tenant_id = $1 ORDER BY name", tenant_id)
    return [dict(row) for row in rows]


def _validate_storage(
    *,
    storage_kind: str,
    cardinality: str,
    source_property: str,
    join_dataset_urn: Optional[str],
    join_source_column: Optional[str],
    join_target_column: Optional[str],
    mid_object_type_urn: Optional[str],
    mid_source_property: Optional[str],
    mid_target_property: Optional[str],
) -> None:
    if storage_kind not in VALID_STORAGE_KINDS:
        raise ValueError(f"invalid storage_kind: {storage_kind!r} (must be one of {sorted(VALID_STORAGE_KINDS)})")
    if storage_kind == "foreign_key":
        if not source_property:
            raise ValueError("source_property is required for foreign_key storage")
        return
    if storage_kind == "join_dataset":
        if cardinality != "many_to_many":
            raise ValueError("join_dataset storage requires cardinality many_to_many")
        if not join_dataset_urn or not join_source_column or not join_target_column:
            raise ValueError("join_dataset requires join_dataset_urn, join_source_column, join_target_column")
        return
    # object_backed
    if not mid_object_type_urn or not mid_source_property or not mid_target_property:
        raise ValueError("object_backed requires mid_object_type_urn, mid_source_property, mid_target_property")


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
    storage_kind: str = "foreign_key",
    join_dataset_urn: Optional[str] = None,
    join_source_column: Optional[str] = None,
    join_target_column: Optional[str] = None,
    mid_object_type: Optional[str] = None,
    mid_source_property: Optional[str] = None,
    mid_target_property: Optional[str] = None,
) -> dict:
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

    mid_urn = None
    if mid_object_type:
        mid_urn = object_type_urn(tenant_id, workspace_id, mid_object_type)
        if await get_object_type(pool, mid_urn) is None:
            raise ValueError(f"mid_object_type does not exist: {mid_object_type}")

    _validate_storage(
        storage_kind=storage_kind,
        cardinality=cardinality,
        source_property=source_property or "",
        join_dataset_urn=join_dataset_urn,
        join_source_column=join_source_column,
        join_target_column=join_target_column,
        mid_object_type_urn=mid_urn,
        mid_source_property=mid_source_property,
        mid_target_property=mid_target_property,
    )

    # FK storage still needs a non-empty source_property; join/object_backed
    # may use a placeholder when the join is not property-on-source.
    effective_source_property = source_property or (
        join_source_column if storage_kind == "join_dataset" else mid_source_property or "_"
    )

    urn = relation_type_urn(tenant_id, workspace_id, name)
    await pool.execute(
        """
        INSERT INTO relation_type (
            urn, tenant_id, name, source_object_type_urn, target_object_type_urn,
            source_property, target_property, cardinality, storage_kind,
            join_dataset_urn, join_source_column, join_target_column,
            mid_object_type_urn, mid_source_property, mid_target_property
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        """,
        urn,
        tenant_id,
        name,
        source_urn,
        target_urn,
        effective_source_property,
        target_property,
        cardinality,
        storage_kind,
        join_dataset_urn,
        join_source_column,
        join_target_column,
        mid_urn,
        mid_source_property,
        mid_target_property,
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
    storage_kind: Optional[str] = None,
    join_dataset_urn: Optional[str] = None,
    join_source_column: Optional[str] = None,
    join_target_column: Optional[str] = None,
    mid_object_type: Optional[str] = None,
    mid_source_property: Optional[str] = None,
    mid_target_property: Optional[str] = None,
) -> dict:
    urn = relation_type_urn(tenant_id, workspace_id, name)
    current = await get_relation_type(pool, urn)
    if current is None:
        raise ValueError(f"unknown RelationType: {name!r}")

    new_target_property = current["target_property"] if target_property is None else target_property
    new_cardinality = current["cardinality"] if cardinality is None else cardinality
    new_storage = current.get("storage_kind") or "foreign_key" if storage_kind is None else storage_kind
    if storage_kind is not None:
        new_storage = storage_kind
    new_join_urn = current.get("join_dataset_urn") if join_dataset_urn is None else join_dataset_urn
    new_join_src = current.get("join_source_column") if join_source_column is None else join_source_column
    new_join_tgt = current.get("join_target_column") if join_target_column is None else join_target_column
    new_mid_src = current.get("mid_source_property") if mid_source_property is None else mid_source_property
    new_mid_tgt = current.get("mid_target_property") if mid_target_property is None else mid_target_property
    new_mid_urn = current.get("mid_object_type_urn")
    if mid_object_type is not None:
        new_mid_urn = object_type_urn(tenant_id, workspace_id, mid_object_type)
        if await get_object_type(pool, new_mid_urn) is None:
            raise ValueError(f"mid_object_type does not exist: {mid_object_type}")

    if not new_target_property:
        raise ValueError("target_property is required — the reverse-direction accessor name")
    if new_cardinality not in VALID_CARDINALITIES:
        raise ValueError(f"invalid cardinality: {new_cardinality!r} (must be one of {sorted(VALID_CARDINALITIES)})")

    _validate_storage(
        storage_kind=new_storage,
        cardinality=new_cardinality,
        source_property=current["source_property"] or "",
        join_dataset_urn=new_join_urn,
        join_source_column=new_join_src,
        join_target_column=new_join_tgt,
        mid_object_type_urn=new_mid_urn,
        mid_source_property=new_mid_src,
        mid_target_property=new_mid_tgt,
    )

    await pool.execute(
        """
        UPDATE relation_type SET
            target_property = $1, cardinality = $2, storage_kind = $3,
            join_dataset_urn = $4, join_source_column = $5, join_target_column = $6,
            mid_object_type_urn = $7, mid_source_property = $8, mid_target_property = $9
        WHERE urn = $10
        """,
        new_target_property,
        new_cardinality,
        new_storage,
        new_join_urn,
        new_join_src,
        new_join_tgt,
        new_mid_urn,
        new_mid_src,
        new_mid_tgt,
        urn,
    )
    return await get_relation_type(pool, urn)
