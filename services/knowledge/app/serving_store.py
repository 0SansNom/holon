"""Serving Store — materialized instance caching and history.

Classifies key-based instance reads as **materialized** (low latency) — not
federated on every request, which is what live scanning alone provided.
Materialization happens once per sync, inside
`catalog._catalogue_sync`'s transaction, not once per read.

Every returned row carries `materializedAt`/`sourceLagSeconds` metadata.
A miss here does not mean the object doesn't exist — it means nothing has been
materialized for that key yet. Callers degrade to a live federated
read via `resolver.py` in that case — see `main._resolve_one`/`_resolve_many`.

`object_instance_history`/`get_instance_as_of` add **transaction-time**
bi-temporal history — "what did the system believe, and when did it record believing it."
This answers "what did we think Customer/4's email was as of March 3rd," not "what was Customer/4's
email valid *for* between two real-world dates."
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import asyncpg

DDL = """
CREATE TABLE IF NOT EXISTS object_instance (
    object_type TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    data JSONB NOT NULL,
    source_snapshot_id BIGINT NOT NULL,
    materialized_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (object_type, tenant_id, instance_id)
);

-- Bi-temporal instance history — transaction-time only. Append-only:
-- unlike `object_instance` above, rows here are never updated or deleted,
-- so "what did the system believe at time T" stays answerable indefinitely.
CREATE TABLE IF NOT EXISTS object_instance_history (
    object_type TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    data JSONB NOT NULL,
    source_snapshot_id BIGINT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


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
    row = await pool.fetchrow(
        "SELECT data, materialized_at FROM object_instance WHERE object_type = $1 AND tenant_id = $2 AND instance_id = $3",
        object_type, tenant_id, str(instance_id),
    )
    if row is None:
        return None
    return _with_freshness(json.loads(row["data"]), row["materialized_at"])


# `instance_id` is TEXT (some ObjectTypes, e.g. InventoryLevel, are keyed
# by a non-numeric SKU) — a plain `::bigint` cast would fail outright for
# those. Numeric ids are zero-padded before the text compare so their
# relative order still matches numeric order; non-numeric ids just sort
# as plain text among themselves. One ORDER BY expression works for both.
_ORDER_BY_INSTANCE_ID = "(CASE WHEN instance_id ~ '^[0-9]+$' THEN lpad(instance_id, 20, '0') ELSE instance_id END)"


async def list_instances(
    pool: asyncpg.Pool, object_type: str, tenant_id: str, *, filter_column: Optional[str] = None, filter_value=None
) -> list[dict]:
    if filter_column is not None:
        rows = await pool.fetch(
            f"""
            SELECT data, materialized_at FROM object_instance
            WHERE object_type = $1 AND tenant_id = $2 AND data->>$3 = $4
            ORDER BY {_ORDER_BY_INSTANCE_ID}
            """,
            object_type, tenant_id, filter_column, str(filter_value),
        )
    else:
        rows = await pool.fetch(
            f"""
            SELECT data, materialized_at FROM object_instance
            WHERE object_type = $1 AND tenant_id = $2
            ORDER BY {_ORDER_BY_INSTANCE_ID}
            """,
            object_type, tenant_id,
        )
    return [_with_freshness(json.loads(row["data"]), row["materialized_at"]) for row in rows]
