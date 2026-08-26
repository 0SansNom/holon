"""Relation link overlays — M:N join_dataset + object_backed link writes.

FK links use `object_instance_edit`. Join / mid links cannot invent Iceberg
rows without RMW races, so writes land here and merge into traversal.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

import asyncpg

async def upsert_link(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    relation_urn: str,
    source_id: str,
    target_id: str,
    op: str,
    set_by_urn: str,
    set_at: datetime,
    mid_id: Optional[str] = None,
) -> None:
    if op not in ("add", "delete"):
        raise ValueError(f"invalid overlay op {op!r}")
    await conn.execute(
        """
        INSERT INTO relation_link_overlay
            (tenant_id, relation_urn, source_id, target_id, op, mid_id, set_by_urn, set_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (tenant_id, relation_urn, source_id, target_id) DO UPDATE SET
            op = EXCLUDED.op,
            mid_id = EXCLUDED.mid_id,
            set_by_urn = EXCLUDED.set_by_urn,
            set_at = EXCLUDED.set_at
        """,
        tenant_id,
        relation_urn,
        str(source_id),
        str(target_id),
        op,
        mid_id,
        set_by_urn,
        set_at,
    )


async def list_overlays(
    pool: asyncpg.Pool, *, tenant_id: str, relation_urn: str
) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT source_id, target_id, op, mid_id
        FROM relation_link_overlay
        WHERE tenant_id = $1 AND relation_urn = $2
        """,
        tenant_id,
        relation_urn,
    )
    return [dict(r) for r in rows]


async def count_overlays(pool: asyncpg.Pool, *, tenant_id: str, relation_urn: str) -> int:
    row = await pool.fetchrow(
        "SELECT count(*) AS n FROM relation_link_overlay WHERE tenant_id = $1 AND relation_urn = $2",
        tenant_id,
        relation_urn,
    )
    return int(row["n"]) if row else 0


def merge_pair_set(
    base_pairs: Iterable[tuple[object, object]],
    overlays: list[dict],
) -> list[tuple[str, str]]:
    """Apply add/delete overlays onto Iceberg (source_id, target_id) pairs."""
    pairs: set[tuple[str, str]] = {(str(s), str(t)) for s, t in base_pairs if s is not None and t is not None}
    for overlay in overlays:
        key = (str(overlay["source_id"]), str(overlay["target_id"]))
        if overlay["op"] == "add":
            pairs.add(key)
        else:
            pairs.discard(key)
    return sorted(pairs)


def overlay_mid_rows(
    overlays: list[dict],
    *,
    src_col: str,
    tgt_col: str,
    current_id,
    filter_is_source: bool,
) -> list[dict]:
    """Synthetic mid Object rows for `op=add` overlays matching current_id."""
    current = str(current_id)
    rows: list[dict] = []
    for overlay in overlays:
        if overlay["op"] != "add":
            continue
        source_id, target_id = str(overlay["source_id"]), str(overlay["target_id"])
        if filter_is_source and source_id != current:
            continue
        if not filter_is_source and target_id != current:
            continue
        mid_id = overlay.get("mid_id") or f"overlay:{source_id}:{target_id}"
        rows.append({"id": mid_id, src_col: source_id, tgt_col: target_id, "_overlay": True})
    return rows


def filter_deleted_mids(
    mid_rows: list[dict],
    overlays: list[dict],
    *,
    src_col: str,
    tgt_col: str,
) -> list[dict]:
    deleted = {
        (str(o["source_id"]), str(o["target_id"]))
        for o in overlays
        if o["op"] == "delete"
    }
    if not deleted:
        return mid_rows
    kept: list[dict] = []
    for row in mid_rows:
        if row.get("_overlay"):
            kept.append(row)
            continue
        key = (str(row.get(src_col)), str(row.get(tgt_col)))
        if key not in deleted:
            kept.append(row)
    return kept
