"""Lineage graph management."""

from __future__ import annotations

import asyncpg

async def record_edge(
    conn: asyncpg.Connection,
    tenant_id: str,
    source_urn: str,
    target_urn: str,
    relation: str,
    *,
    source_column: str = "",
    target_property: str = "",
) -> None:
    await conn.execute(
        """
        INSERT INTO lineage_edge (tenant_id, source_urn, target_urn, relation, source_column, target_property)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (source_urn, target_urn, relation, source_column, target_property) DO NOTHING
        """,
        tenant_id,
        source_urn,
        target_urn,
        relation,
        source_column,
        target_property,
    )


async def edges_touching(pool: asyncpg.Pool, tenant_id: str, urn: str) -> list[dict]:
    """Edges touching `urn`, collapsed to the current state on the
    `target_urn = $2` side only.

    Every connector sync mints a fresh `dataset_version_urn` and records a
    brand-new `maps_to` edge from it to the (stable) ObjectType — by
    design, per this module's docstring, lineage is never hand-declared,
    only captured from execution. Nothing ever prunes the old edges, so a
    resynced-many-times dataset accumulates one dead source node per past
    sync. `(relation, source_column, target_property)` is the same slot
    across versions (this build maps each property from exactly one
    dataset's column, so two distinct live sources never share a slot),
    so keeping only the newest row per slot is a correctness-preserving
    view of "current lineage", not a heuristic — the full history stays
    queryable by `dataset_version_urn` directly (see `test_column_lineage.py`),
    just not repeated here as noise. The `source_urn = $2` side (e.g.
    walking a pipeline DAG downstream) has no such duplication and is
    left untouched.
    """
    rows = await pool.fetch(
        """
        WITH by_target AS (
            SELECT DISTINCT ON (relation, source_column, target_property)
                source_urn, target_urn, relation, source_column, target_property, created_at
            FROM lineage_edge
            WHERE tenant_id = $1 AND target_urn = $2
            ORDER BY relation, source_column, target_property, created_at DESC
        ),
        by_source AS (
            SELECT source_urn, target_urn, relation, source_column, target_property, created_at
            FROM lineage_edge
            WHERE tenant_id = $1 AND source_urn = $2
        )
        SELECT * FROM by_target
        UNION ALL
        SELECT * FROM by_source
        ORDER BY created_at
        """,
        tenant_id,
        urn,
    )
    return [dict(row) for row in rows]
