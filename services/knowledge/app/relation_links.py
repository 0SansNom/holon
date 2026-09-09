"""Materialized M:N join_dataset pairs — serving-store equivalent for bridges.

Catalog ingest writes these from Iceberg once per sync. Object-graph reads
query Postgres (then apply `relation_link_overlay`), never scan the warehouse
on the request path.
"""

from __future__ import annotations

from typing import Iterable

import asyncpg

_BATCH = 1000


def pairs_from_join_rows(
    rows: Iterable[dict], source_column: str, target_column: str
) -> list[tuple[str, str]]:
    """Extract unique (source_id, target_id) pairs from a join-table scan."""
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []
    for row in rows:
        source_id, target_id = row.get(source_column), row.get(target_column)
        if source_id is None or target_id is None:
            continue
        key = (str(source_id), str(target_id))
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)
    return pairs


async def delete_pairs(
    conn: asyncpg.Connection, *, tenant_id: str, relation_urn: str
) -> None:
    await conn.execute(
        "DELETE FROM relation_link WHERE tenant_id = $1 AND relation_urn = $2",
        tenant_id,
        relation_urn,
    )
    await conn.execute(
        "DELETE FROM relation_link_sync WHERE tenant_id = $1 AND relation_urn = $2",
        tenant_id,
        relation_urn,
    )


async def get_sync_snapshot(
    pool: asyncpg.Pool, *, tenant_id: str, relation_urn: str
) -> int | None:
    row = await pool.fetchrow(
        "SELECT source_snapshot_id FROM relation_link_sync WHERE tenant_id = $1 AND relation_urn = $2",
        tenant_id,
        relation_urn,
    )
    if row is None or row["source_snapshot_id"] is None:
        return None
    return int(row["source_snapshot_id"])


async def upsert_sync(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    relation_urn: str,
    snapshot_id: int,
    pair_count: int,
) -> None:
    await conn.execute(
        """
        INSERT INTO relation_link_sync (
            tenant_id, relation_urn, source_snapshot_id, pair_count, materialized_at
        )
        VALUES ($1, $2, $3, $4, now())
        ON CONFLICT (tenant_id, relation_urn) DO UPDATE SET
            source_snapshot_id = EXCLUDED.source_snapshot_id,
            pair_count = EXCLUDED.pair_count,
            materialized_at = now()
        """,
        tenant_id,
        relation_urn,
        snapshot_id,
        pair_count,
    )


async def replace_pairs(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    relation_urn: str,
    snapshot_id: int,
    pairs: list[tuple[str, str]],
) -> None:
    """Full replace for one RelationType — join datasets overwrite per snapshot."""
    await delete_pairs(conn, tenant_id=tenant_id, relation_urn=relation_urn)
    for offset in range(0, len(pairs), _BATCH):
        chunk = pairs[offset : offset + _BATCH]
        source_ids = [s for s, _t in chunk]
        target_ids = [t for _s, t in chunk]
        await conn.execute(
            """
            INSERT INTO relation_link (
                tenant_id, relation_urn, source_id, target_id, source_snapshot_id
            )
            SELECT $1, $2, x.source_id, x.target_id, $3
            FROM unnest($4::text[], $5::text[]) AS x(source_id, target_id)
            ON CONFLICT (tenant_id, relation_urn, source_id, target_id) DO UPDATE SET
                source_snapshot_id = EXCLUDED.source_snapshot_id,
                materialized_at = now()
            """,
            tenant_id,
            relation_urn,
            snapshot_id,
            source_ids,
            target_ids,
        )
    await upsert_sync(
        conn,
        tenant_id=tenant_id,
        relation_urn=relation_urn,
        snapshot_id=snapshot_id,
        pair_count=len(pairs),
    )


async def list_neighbor_ids(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    relation_urn: str,
    current_id,
    as_source: bool,
) -> list[str]:
    """Neighbor ids of one instance. `as_source=True` → this side is the source."""
    current = str(current_id)
    if as_source:
        rows = await pool.fetch(
            """
            SELECT target_id FROM relation_link
            WHERE tenant_id = $1 AND relation_urn = $2 AND source_id = $3
            """,
            tenant_id,
            relation_urn,
            current,
        )
        return [row["target_id"] for row in rows]
    rows = await pool.fetch(
        """
        SELECT source_id FROM relation_link
        WHERE tenant_id = $1 AND relation_urn = $2 AND target_id = $3
        """,
        tenant_id,
        relation_urn,
        current,
    )
    return [row["source_id"] for row in rows]
