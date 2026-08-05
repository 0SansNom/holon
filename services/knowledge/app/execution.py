"""Execution — the ExecutionPlan / Adapter abstraction.

Deliberately minimal, scoped to what this build's own data actually needs:

- **Two operators**: equality filter, and count-by-filter. A general multi-operator
  DAG compiler is a project of its own — out of proportion here.
- **DuckDB as the built-in adapter**: DuckDB handles interactive queries for
  datasets in this build. An **execution adapter plugin type** (`execution_adapter_registry.py`)
  allows an ObjectType with an active adapter registration to route through it
  instead, proving the interface is genuinely swappable without touching
  `get_or_execute`'s caching/audit logic at all.
- **Knowledge-owned, not a separate container**: wrapping DuckDB directly in Knowledge
  avoids adding unnecessary containers while maintaining a clear execution interface.

Plan-hash caching: A plan's hash
is computed over its inputs *including the exact DatasetVersion it
reads* *and* the operator itself, so
an identical query against unchanged data is never re-executed, a filter
and a count over the same inputs get distinct cache entries, and a
genuinely new sync changes the hash and correctly forces re-execution —
content-addressed caching with no explicit invalidation logic.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Optional

import asyncpg
import duckdb

from holon_common import build_urn

from . import catalog, execution_adapter_registry, resolver

DDL = """
CREATE TABLE IF NOT EXISTS execution_run (
    plan_hash TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    plan JSONB NOT NULL,
    result JSONB NOT NULL,
    row_count INTEGER NOT NULL,
    executed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    cache_hits INTEGER NOT NULL DEFAULT 0
);
"""

# object_type_name -> dataset_name, built once from the registry catalog.py
# already maintains — no separate mapping to keep in sync.
_OBJECT_TYPE_TO_DATASET = {
    spec["object_type_name"]: dataset_name for dataset_name, spec in catalog.DATASET_OBJECT_TYPES.items()
}


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


def _compute_plan_hash(
    *, object_type_urn: str, dataset_version_urn: str, filter_property: str, filter_value: str, operation: str = "filter"
) -> str:
    canonical = json.dumps(
        {
            "object_type_urn": object_type_urn,
            "dataset_version_urn": dataset_version_urn,
            "filter_property": filter_property,
            "filter_value": filter_value,
            "operation": operation,
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _execute_duckdb_operation(
    dataset_name: str,
    source_column: str,
    filter_value: str,
    operation: str,
    iceberg_config: dict,
    *,
    snapshot_id: Optional[int] = None,
):
    """The synchronous half of the DuckDB adapter, run through
    `asyncio.to_thread` by its callers below. `snapshot_id` option:
    omitted, this scans the table's current state (the normal fresh
    execution path); given, it pins the scan to that exact historical
    snapshot (`replay` below) — the only difference between "execute" and
    "replay" is which snapshot DuckDB sees. `operation="count"` returns a
    single `{"count": N}` dict instead of matching rows.
    """
    arrow_table = resolver.scan_at(dataset_name, snapshot_id=snapshot_id, **iceberg_config).to_arrow()
    con = duckdb.connect()
    con.register("t", arrow_table)
    if operation == "count":
        (count,) = con.execute(f"SELECT COUNT(*) FROM t WHERE {source_column} = ?", [filter_value]).fetchone()
        return {"count": count}
    return con.execute(f"SELECT * FROM t WHERE {source_column} = ?", [filter_value]).fetch_arrow_table().to_pylist()


def _json_default(o: object) -> str:
    """ISO 8601 for temporal values (matches FastAPI's own serialization of
    the fresh path), str() for everything else (Decimal & co)."""
    isoformat = getattr(o, "isoformat", None)
    return isoformat() if callable(isoformat) else str(o)


async def get_or_execute(
    pool: asyncpg.Pool,
    iceberg_config: dict,
    *,
    tenant_id: str,
    workspace_id: str,
    object_type_name: str,
    object_type_urn: str,
    property_mapping: dict,
    filter_property: str,
    filter_value: str,
    operation: str = "filter",
) -> dict:
    if operation not in ("filter", "count"):
        raise ValueError(f"unknown operation {operation!r} (must be 'filter' or 'count')")
    if filter_property not in property_mapping:
        raise ValueError(f"unknown property {filter_property!r} on {object_type_name} (known: {sorted(property_mapping)})")
    source_column = property_mapping[filter_property]

    dataset_name = _OBJECT_TYPE_TO_DATASET[object_type_name]
    dataset_urn = build_urn(tenant_id, workspace_id, "dataset", dataset_name)
    dataset_version_urn = await catalog.latest_dataset_version_urn(pool, dataset_urn)
    if dataset_version_urn is None:
        raise ValueError(f"{object_type_name} has never been synced — nothing to execute against")

    plan_hash = _compute_plan_hash(
        object_type_urn=object_type_urn,
        dataset_version_urn=dataset_version_urn,
        filter_property=filter_property,
        filter_value=filter_value,
        operation=operation,
    )

    cached = await pool.fetchrow("SELECT plan, result, row_count FROM execution_run WHERE plan_hash = $1", plan_hash)
    if cached is not None:
        await pool.execute("UPDATE execution_run SET cache_hits = cache_hits + 1 WHERE plan_hash = $1", plan_hash)
        plan = json.loads(cached["plan"])
        result = json.loads(cached["result"])
        if operation == "count":
            return {"planId": plan["plan_id"], "planHash": plan_hash, "cached": True, "count": result["count"]}
        return {
            "planId": plan["plan_id"],
            "planHash": plan_hash,
            "cached": True,
            "rowCount": cached["row_count"],
            "results": result,
        }

    # An ObjectType with an active execution
    # adapter plugin routes through it instead of the built-in DuckDB
    # adapter; everything below (plan-hash caching, `execution_run`
    # audit) is identical either way — proving the adapter *interface* is
    # genuinely swappable, not a parallel code path.
    adapter_plugin = await execution_adapter_registry.find_active_adapter_for_object_type(pool, object_type_name)
    if adapter_plugin is not None:
        raw_result = await adapter_plugin.execute(
            pool,
            object_type=object_type_name,
            tenant_id=tenant_id,
            filter_property=filter_property,
            filter_value=filter_value,
            operation=operation,
        )
    else:
        # The DuckDB adapter — the only *built-in* engine this build's data
        # volume ever needs. `_load_table` is the exact call every
        # `resolver.fetch_*` already makes; reused as-is, not duplicated.
        # pyiceberg/DuckDB are synchronous, so this runs via
        # asyncio.to_thread — calling it directly would block Knowledge's
        # whole event loop for the scan's duration (see main.py's module
        # docstring for why every synchronous call here goes through it).
        raw_result = await asyncio.to_thread(
            _execute_duckdb_operation, dataset_name, source_column, filter_value, operation, iceberg_config
        )

    # Normalize through JSON *once* and serve that exact form on both the
    # fresh and the cached path. Without this the two paths disagree:
    # fresh rows carry real datetimes (FastAPI serializes them ISO) while
    # the cached copy went through `default=str` — a cache hit must be
    # indistinguishable from a fresh run.
    normalized = json.loads(json.dumps(raw_result, default=_json_default))

    plan_id = plan_hash[:16]
    plan = {
        "plan_id": plan_id,
        "object_type": object_type_name,
        "dataset_version_urn": dataset_version_urn,
        "filter_property": filter_property,
        "filter_value": filter_value,
        # Stored so `replay()` is fully self-contained from the frozen
        # plan alone — it must not need to re-derive
        # this from the ObjectType's *current* property mapping, which
        # could itself have changed since this plan was first run.
        "source_column": source_column,
        "dataset_name": dataset_name,
        "operation": operation,
    }
    row_count = normalized["count"] if operation == "count" else len(normalized)
    await pool.execute(
        """
        INSERT INTO execution_run (plan_hash, tenant_id, plan, result, row_count)
        VALUES ($1, $2, $3::jsonb, $4::jsonb, $5)
        ON CONFLICT (plan_hash) DO NOTHING
        """,
        plan_hash,
        tenant_id,
        json.dumps(plan),
        json.dumps(normalized),
        row_count,
    )

    if operation == "count":
        return {"planId": plan_id, "planHash": plan_hash, "cached": False, "count": normalized["count"]}
    return {"planId": plan_id, "planHash": plan_hash, "cached": False, "rowCount": len(normalized), "results": normalized}


async def replay(pool: asyncpg.Pool, iceberg_config: dict, *, plan_hash: str) -> dict:
    """Re-executes a
    previously-run, frozen plan against the *exact historical Iceberg
    snapshot* it originally pinned (via the stored `dataset_version_urn`),
    not whatever that dataset looks like now — genuinely proving
    reproducibility rather than trivially matching because nothing
    changed since.
    """
    row = await pool.fetchrow("SELECT plan, result FROM execution_run WHERE plan_hash = $1", plan_hash)
    if row is None:
        raise ValueError(f"no execution_run found for plan_hash {plan_hash!r}")

    plan = json.loads(row["plan"])
    original_result = json.loads(row["result"])
    operation = plan.get("operation", "filter")  # plans frozen before this field existed default to "filter"

    dataset_version = await catalog.get_dataset_version_by_urn(pool, plan["dataset_version_urn"])
    if dataset_version is None:
        raise ValueError(f"pinned dataset_version {plan['dataset_version_urn']!r} no longer exists")

    raw_result = await asyncio.to_thread(
        _execute_duckdb_operation,
        plan["dataset_name"],
        plan["source_column"],
        plan["filter_value"],
        operation,
        iceberg_config,
        snapshot_id=dataset_version["snapshot_id"],
    )
    replayed_result = json.loads(json.dumps(raw_result, default=_json_default))

    return {
        "planHash": plan_hash,
        "reproducible": replayed_result == original_result,
        "result": replayed_result,
        "originalResult": original_result,
    }
