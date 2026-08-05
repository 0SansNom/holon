"""Lineage graph management.

Lineage MUST be captured automatically from execution, never
hand-declared. In this build the "execution" is a single connector sync,
so every edge is recorded as a direct side effect of cataloguing a new
DatasetVersion — never typed in by an operator.

Minimum granularity is dataset, target granularity is column. Both
coexist here: a coarse dataset-level edge (`source_column`/
`target_property` left as `''`, the "not applicable" sentinel — kept as
an empty string rather than NULL so the uniqueness constraint below stays
a plain column list, not a NULL-aware expression) plus one column-level
edge per mapped property.
"""

from __future__ import annotations

import asyncpg

DDL = """
CREATE TABLE IF NOT EXISTS lineage_edge (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    source_urn TEXT NOT NULL,
    target_urn TEXT NOT NULL,
    relation TEXT NOT NULL,
    source_column TEXT NOT NULL DEFAULT '',
    target_property TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_urn, target_urn, relation)
);

-- additive migrations for databases seeded before column-level lineage existed
ALTER TABLE lineage_edge ADD COLUMN IF NOT EXISTS source_column TEXT NOT NULL DEFAULT '';
ALTER TABLE lineage_edge ADD COLUMN IF NOT EXISTS target_property TEXT NOT NULL DEFAULT '';
ALTER TABLE lineage_edge DROP CONSTRAINT IF EXISTS lineage_edge_source_urn_target_urn_relation_key;
ALTER TABLE lineage_edge DROP CONSTRAINT IF EXISTS lineage_edge_full_key;
ALTER TABLE lineage_edge ADD CONSTRAINT lineage_edge_full_key
    UNIQUE (source_urn, target_urn, relation, source_column, target_property);
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


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
    rows = await pool.fetch(
        """
        SELECT source_urn, target_urn, relation, source_column, target_property, created_at
        FROM lineage_edge
        WHERE tenant_id = $1 AND (source_urn = $2 OR target_urn = $2)
        ORDER BY created_at
        """,
        tenant_id,
        urn,
    )
    return [dict(row) for row in rows]
