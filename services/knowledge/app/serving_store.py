"""Serving Store — materialized instance caching and bi-temporal history."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import asyncpg

async def is_tombstoned(
    pool: asyncpg.Pool, object_type: str, tenant_id: str, instance_id
) -> bool:
    return bool(
        await pool.fetchval(
            "SELECT 1 FROM object_instance_tombstone WHERE tenant_id = $1 AND object_type = $2 AND instance_id = $3",
            tenant_id,
            object_type,
            str(instance_id),
        )
    )


async def list_tombstoned_ids(pool: asyncpg.Pool, object_type: str, tenant_id: str) -> set[str]:
    """Bulk tombstone lookup when filtering a collection that has no
    serving-store row to join through `NOT EXISTS` the way `list_instances`
    does — one query per collection instead of one per row.
    """
    rows = await pool.fetch(
        "SELECT instance_id FROM object_instance_tombstone WHERE tenant_id = $1 AND object_type = $2",
        tenant_id, object_type,
    )
    return {row["instance_id"] for row in rows}


async def materialize(
    conn: asyncpg.Connection, *, object_type: str, tenant_id: str, snapshot_id: int, rows: list[dict]
) -> None:
    """Transaction-time history: every materialization also appends
    one row to `object_instance_history`, in addition to the existing
    upsert into `object_instance` below. Deliberately no dedup-if-unchanged
    check — kept simple; a row recorded even when nothing changed is still
    valid history ("still true as of this sync"), and growth stays bounded
    by how often each dataset actually syncs (low for the batch-synced
    types, only on real change for the streaming one).
    """
    for row in rows:
        instance_id = str(row["id"])
        payload = json.dumps(row, default=str)
        await conn.execute(
            """
            INSERT INTO object_instance (object_type, tenant_id, instance_id, data, source_snapshot_id, materialized_at)
            VALUES ($1, $2, $3, $4::jsonb, $5, now())
            ON CONFLICT (object_type, tenant_id, instance_id) DO UPDATE SET
                data = EXCLUDED.data,
                source_snapshot_id = EXCLUDED.source_snapshot_id,
                materialized_at = EXCLUDED.materialized_at
            """,
            object_type,
            tenant_id,
            instance_id,
            payload,
            snapshot_id,
        )
        await conn.execute(
            """
            INSERT INTO object_instance_history (object_type, tenant_id, instance_id, data, source_snapshot_id)
            VALUES ($1, $2, $3, $4::jsonb, $5)
            """,
            object_type,
            tenant_id,
            instance_id,
            payload,
            snapshot_id,
        )


def _with_freshness(data: dict, materialized_at: datetime) -> dict:
    data = dict(data)
    data["materializedAt"] = materialized_at.isoformat()
    data["sourceLagSeconds"] = max(0, int((datetime.now(timezone.utc) - materialized_at).total_seconds()))
    data["degraded"] = False
    return data


async def get_instance_as_of(
    pool: asyncpg.Pool, object_type: str, tenant_id: str, instance_id, as_of: datetime
) -> Optional[dict]:
    """The latest historical row recorded at or before `as_of`. No
    federated fallback here: a historical read either has history to
    answer from or it doesn't (degrading to "current" would silently
    answer a different question than the one asked).
    """
    row = await pool.fetchrow(
        """
        SELECT data, recorded_at FROM object_instance_history
        WHERE object_type = $1 AND tenant_id = $2 AND instance_id = $3 AND recorded_at <= $4
        ORDER BY recorded_at DESC LIMIT 1
        """,
        object_type, tenant_id, str(instance_id), as_of,
    )
    if row is None:
        return None
    data = dict(json.loads(row["data"]))
    data["materializedAt"] = row["recorded_at"].isoformat()
    data["sourceLagSeconds"] = max(0, int((datetime.now(timezone.utc) - row["recorded_at"]).total_seconds()))
    data["degraded"] = False
    data["asOf"] = as_of.isoformat()
    return data


async def get_instance(pool: asyncpg.Pool, object_type: str, tenant_id: str, instance_id) -> Optional[dict]:
    if await is_tombstoned(pool, object_type, tenant_id, instance_id):
        return None
    row = await pool.fetchrow(
        "SELECT data, materialized_at FROM object_instance WHERE object_type = $1 AND tenant_id = $2 AND instance_id = $3",
        object_type, tenant_id, str(instance_id),
    )
    if row is None:
        return None
    return _with_freshness(json.loads(row["data"]), row["materialized_at"])


# Zero-pad numeric IDs so ordering matches numeric order, sorting non-numeric IDs as text.
_ORDER_BY_INSTANCE_ID = "(CASE WHEN instance_id ~ '^[0-9]+$' THEN lpad(instance_id, 20, '0') ELSE instance_id END)"


async def list_instances(
    pool: asyncpg.Pool,
    object_type: str,
    tenant_id: str,
    *,
    filter_column: Optional[str] = None,
    filter_value=None,
    after_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict]:
    """List materialized instances, optionally keyset-paged.

    `after_id` is exclusive (rows strictly after that instance_id in the
    same order as `_ORDER_BY_INSTANCE_ID`). `limit` caps SQL rows before
    freshness wrapping — callers that post-filter (markings) should
    over-fetch.
    """
    params: list = [object_type, tenant_id]
    filter_sql = ""
    if filter_column is not None:
        params.extend([filter_column, str(filter_value)])
        filter_sql = "AND data->>$3 = $4"

    after_sql = ""
    if after_id is not None:
        params.append(str(after_id))
        p = len(params)
        after_sql = f"""
          AND {_ORDER_BY_INSTANCE_ID} > (
            CASE WHEN ${p}::text ~ '^[0-9]+$' THEN lpad(${p}::text, 20, '0') ELSE ${p}::text END
          )
        """

    limit_sql = ""
    if limit is not None:
        params.append(int(limit))
        limit_sql = f"LIMIT ${len(params)}"

    rows = await pool.fetch(
        f"""
        SELECT data, materialized_at FROM object_instance oi
        WHERE object_type = $1 AND tenant_id = $2
          {filter_sql}
          AND NOT EXISTS (
            SELECT 1 FROM object_instance_tombstone t
            WHERE t.tenant_id = oi.tenant_id AND t.object_type = oi.object_type AND t.instance_id = oi.instance_id
          )
          {after_sql}
        ORDER BY {_ORDER_BY_INSTANCE_ID}
        {limit_sql}
        """,
        *params,
    )
    return [_with_freshness(json.loads(row["data"]), row["materialized_at"]) for row in rows]
