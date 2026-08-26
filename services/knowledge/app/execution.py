"""Execution engine — ExecutionPlan execution via DuckDB and execution adapters.

Handles point filtering, counting, group-by aggregation, and joins with plan-hash caching.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Optional

import asyncpg
import duckdb

from holon_common import build_urn
from holon_common.sql_ident import quote_identifier

from . import catalog, execution_adapter_registry, ontology, resolver

_VALID_OPERATIONS = ("filter", "count", "group_by", "join")
_VALID_AGGREGATE_FUNCTIONS = ("count", "sum", "avg", "min", "max")


def _compute_plan_hash(*, object_type_urn: str, operation: str, **operation_fields) -> str:
    canonical = json.dumps(
        {"object_type_urn": object_type_urn, "operation": operation, **operation_fields}, sort_keys=True
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
    aggregate_source_column: Optional[str] = None,
    aggregate_function: str = "count",
):
    """The synchronous half of the DuckDB adapter, run through
    `asyncio.to_thread` by its callers below. `snapshot_id` option:
    omitted, this scans the table's current state (the normal fresh
    execution path); given, it pins the scan to that exact historical
    snapshot (`replay` below) — the only difference between "execute" and
    "replay" is which snapshot DuckDB sees. `operation="count"` returns a
    single `{"count": N}` dict instead of matching rows; `operation=
    "group_by"` reuses `source_column` as the *group-by* column (same
    param slot, no signature growth) and returns one `{"group": ...,
    "aggregate": ...}` row per distinct group value.
    """
    arrow_table = resolver.scan_at(dataset_name, snapshot_id=snapshot_id, **iceberg_config).to_arrow()
    con = duckdb.connect()
    con.register("t", arrow_table)
    src = quote_identifier(source_column)
    if operation == "count":
        (count,) = con.execute(f"SELECT COUNT(*) FROM t WHERE {src} = ?", [filter_value]).fetchone()
        return {"count": count}
    if operation == "group_by":
        agg_expr = "COUNT(*)" if aggregate_function == "count" else f"{aggregate_function.upper()}({quote_identifier(aggregate_source_column)})"
        query = f"SELECT {src} AS group_value, {agg_expr} AS aggregate_value FROM t GROUP BY {src} ORDER BY {src}"
        rows = con.execute(query).fetch_arrow_table().to_pylist()
        return [{"group": row["group_value"], "aggregate": row["aggregate_value"]} for row in rows]
    return con.execute(f"SELECT * FROM t WHERE {src} = ?", [filter_value]).fetch_arrow_table().to_pylist()


def _execute_duckdb_join(
    source_dataset_name: str,
    source_join_column: str,
    source_columns: list[str],
    target_dataset_name: str,
    target_id_column: str,
    target_columns: list[str],
    iceberg_config: dict,
    *,
    source_snapshot_id: Optional[int] = None,
    target_snapshot_id: Optional[int] = None,
):
    """The join operator's own executor, kept separate from
    `_execute_duckdb_operation` rather than folded in — it scans *two*
    tables, not one, a different enough shape (two Arrow registrations,
    an explicit column-prefixed `SELECT` to keep same-named columns on
    both sides — every ObjectType's own `id` included — from colliding
    or silently shadowing one another in the result).
    """
    source_arrow = resolver.scan_at(source_dataset_name, snapshot_id=source_snapshot_id, **iceberg_config).to_arrow()
    target_arrow = resolver.scan_at(target_dataset_name, snapshot_id=target_snapshot_id, **iceberg_config).to_arrow()
    con = duckdb.connect()
    con.register("s", source_arrow)
    con.register("t", target_arrow)
    source_select = ", ".join(f's.{quote_identifier(col)} AS "s_{col}"' for col in source_columns)
    target_select = ", ".join(f't.{quote_identifier(col)} AS "t_{col}"' for col in target_columns)
    query = (
        f"SELECT {source_select}, {target_select} FROM s "
        f"JOIN t ON s.{quote_identifier(source_join_column)} = t.{quote_identifier(target_id_column)}"
    )
    return con.execute(query).fetch_arrow_table().to_pylist()


def _execute_duckdb_bridged_join(
    source_dataset_name: str,
    source_id_column: str,
    source_columns: list[str],
    bridge_dataset_name: str,
    bridge_source_column: str,
    bridge_target_column: str,
    target_dataset_name: str,
    target_id_column: str,
    target_columns: list[str],
    iceberg_config: dict,
    *,
    source_snapshot_id: Optional[int] = None,
    bridge_snapshot_id: Optional[int] = None,
    target_snapshot_id: Optional[int] = None,
):
    """M:N join via a bridge table (join_dataset) or mid ObjectType
    dataset (object_backed): ``s.pk = bridge.src AND bridge.tgt = t.pk``.
    Result shape matches FK join (`s_` / `t_` prefixes) so masking stays
    identical.
    """
    source_arrow = resolver.scan_at(source_dataset_name, snapshot_id=source_snapshot_id, **iceberg_config).to_arrow()
    bridge_arrow = resolver.scan_at(bridge_dataset_name, snapshot_id=bridge_snapshot_id, **iceberg_config).to_arrow()
    target_arrow = resolver.scan_at(target_dataset_name, snapshot_id=target_snapshot_id, **iceberg_config).to_arrow()
    con = duckdb.connect()
    con.register("s", source_arrow)
    con.register("j", bridge_arrow)
    con.register("t", target_arrow)
    source_select = ", ".join(f's.{quote_identifier(col)} AS "s_{col}"' for col in source_columns)
    target_select = ", ".join(f't.{quote_identifier(col)} AS "t_{col}"' for col in target_columns)
    query = (
        f"SELECT {source_select}, {target_select} FROM s "
        f"JOIN j ON s.{quote_identifier(source_id_column)} = j.{quote_identifier(bridge_source_column)} "
        f"JOIN t ON j.{quote_identifier(bridge_target_column)} = t.{quote_identifier(target_id_column)}"
    )
    return con.execute(query).fetch_arrow_table().to_pylist()


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
    operation: str = "filter",
    filter_property: Optional[str] = None,
    filter_value: Optional[str] = None,
    group_by_property: Optional[str] = None,
    aggregate_property: Optional[str] = None,
    aggregate_function: str = "count",
    relation_name: Optional[str] = None,
    join_source_property: Optional[str] = None,
    target_object_type_name: Optional[str] = None,
    target_property_mapping: Optional[dict] = None,
    storage_kind: str = "foreign_key",
    bridge_dataset_name: Optional[str] = None,
    bridge_source_column: Optional[str] = None,
    bridge_target_column: Optional[str] = None,
    source_id_column: Optional[str] = None,
) -> dict:
    if operation not in _VALID_OPERATIONS:
        raise ValueError(f"unknown operation {operation!r} (must be one of {_VALID_OPERATIONS})")
    iceberg_config = {**iceberg_config, "tenant_id": tenant_id}

    object_type_row = await ontology.get_object_type(pool, object_type_urn)
    if object_type_row is None:
        raise ValueError(f"unknown ObjectType: {object_type_name}")
    dataset_name = object_type_row["source_dataset_urn"].rsplit(":", 1)[-1]
    dataset_urn = build_urn(tenant_id, workspace_id, "dataset", dataset_name)
    dataset_version_urn = await catalog.latest_dataset_version_urn(pool, dataset_urn)
    if dataset_version_urn is None:
        raise ValueError(f"{object_type_name} has never been synced — nothing to execute against")

    target_dataset_name = target_dataset_version_urn = target_id_column = None
    bridge_dataset_version_urn = None
    aggregate_source_column = None
    source_column = None

    if operation in ("filter", "count"):
        if filter_property not in property_mapping:
            raise ValueError(f"unknown property {filter_property!r} on {object_type_name} (known: {sorted(property_mapping)})")
        source_column = property_mapping[filter_property]
        hash_fields = {"dataset_version_urn": dataset_version_urn, "filter_property": filter_property, "filter_value": filter_value}

    elif operation == "group_by":
        if group_by_property not in property_mapping:
            raise ValueError(f"unknown property {group_by_property!r} on {object_type_name} (known: {sorted(property_mapping)})")
        if aggregate_function not in _VALID_AGGREGATE_FUNCTIONS:
            raise ValueError(f"unknown aggregate_function {aggregate_function!r} (must be one of {_VALID_AGGREGATE_FUNCTIONS})")
        source_column = property_mapping[group_by_property]
        if aggregate_function != "count":
            if not aggregate_property or aggregate_property not in property_mapping:
                raise ValueError(
                    f"aggregate_function {aggregate_function!r} requires a known aggregate_property on {object_type_name}"
                )
            aggregate_source_column = property_mapping[aggregate_property]
        hash_fields = {
            "dataset_version_urn": dataset_version_urn,
            "group_by_property": group_by_property,
            "aggregate_property": aggregate_property,
            "aggregate_function": aggregate_function,
        }

    else:  # join
        if not relation_name or not target_object_type_name or not target_property_mapping:
            raise ValueError(
                "join requires relation_name, target_object_type_name, and target_property_mapping"
            )
        if "id" not in target_property_mapping:
            raise ValueError(f"{target_object_type_name} has no 'id' property to join against")
        target_object_type_row = await ontology.get_object_type(
            pool, ontology.object_type_urn(tenant_id, workspace_id, target_object_type_name)
        )
        if target_object_type_row is None:
            raise ValueError(f"unknown ObjectType: {target_object_type_name}")
        target_dataset_name = target_object_type_row["source_dataset_urn"].rsplit(":", 1)[-1]
        target_dataset_urn = build_urn(tenant_id, workspace_id, "dataset", target_dataset_name)
        target_dataset_version_urn = await catalog.latest_dataset_version_urn(pool, target_dataset_urn)
        if target_dataset_version_urn is None:
            raise ValueError(f"{target_object_type_name} has never been synced — nothing to join against")
        target_id_column = target_property_mapping["id"]

        kind = storage_kind or "foreign_key"
        if kind == "foreign_key":
            if not join_source_property:
                raise ValueError("foreign_key join requires join_source_property")
            if join_source_property not in property_mapping:
                raise ValueError(
                    f"unknown property {join_source_property!r} on {object_type_name} (known: {sorted(property_mapping)})"
                )
            source_column = property_mapping[join_source_property]
            hash_fields = {
                "dataset_version_urn": dataset_version_urn,
                "relation_name": relation_name,
                "storage_kind": kind,
                "join_source_property": join_source_property,
                "target_object_type": target_object_type_name,
                "target_dataset_version_urn": target_dataset_version_urn,
            }
        elif kind in ("join_dataset", "object_backed"):
            if not bridge_dataset_name or not bridge_source_column or not bridge_target_column or not source_id_column:
                raise ValueError(
                    f"{kind} join requires bridge_dataset_name, bridge_source_column, "
                    "bridge_target_column, and source_id_column"
                )
            bridge_dataset_urn = build_urn(tenant_id, workspace_id, "dataset", bridge_dataset_name)
            bridge_dataset_version_urn = await catalog.latest_dataset_version_urn(pool, bridge_dataset_urn)
            if bridge_dataset_version_urn is None:
                raise ValueError(
                    f"bridge dataset {bridge_dataset_name!r} has never been synced — nothing to join through"
                )
            source_column = source_id_column
            hash_fields = {
                "dataset_version_urn": dataset_version_urn,
                "relation_name": relation_name,
                "storage_kind": kind,
                "bridge_dataset_name": bridge_dataset_name,
                "bridge_source_column": bridge_source_column,
                "bridge_target_column": bridge_target_column,
                "source_id_column": source_id_column,
                "bridge_dataset_version_urn": bridge_dataset_version_urn,
                "target_object_type": target_object_type_name,
                "target_dataset_version_urn": target_dataset_version_urn,
            }
        else:
            raise ValueError(f"unsupported join storage_kind: {kind!r}")

    plan_hash = _compute_plan_hash(object_type_urn=object_type_urn, operation=operation, **hash_fields)

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
    # adapter (`filter`/`count` only, see module docstring); everything
    # below (plan-hash caching, `execution_run` audit) is identical
    # either way — proving the adapter *interface* is genuinely
    # swappable, not a parallel code path.
    adapter_plugin = (
        await execution_adapter_registry.find_active_adapter_for_object_type(pool, object_type_name)
        if operation in ("filter", "count")
        else None
    )
    if adapter_plugin is not None:
        raw_result = await adapter_plugin.execute(
            pool,
            object_type=object_type_name,
            tenant_id=tenant_id,
            filter_property=filter_property,
            filter_value=filter_value,
            operation=operation,
        )
    elif operation == "join":
        kind = storage_kind or "foreign_key"
        if kind == "foreign_key":
            raw_result = await asyncio.to_thread(
                _execute_duckdb_join,
                dataset_name, source_column, list(property_mapping.values()),
                target_dataset_name, target_id_column, list(target_property_mapping.values()),
                iceberg_config,
            )
        else:
            raw_result = await asyncio.to_thread(
                _execute_duckdb_bridged_join,
                dataset_name, source_id_column, list(property_mapping.values()),
                bridge_dataset_name, bridge_source_column, bridge_target_column,
                target_dataset_name, target_id_column, list(target_property_mapping.values()),
                iceberg_config,
            )
    else:
        # The DuckDB adapter for processing operations.
        # pyiceberg/DuckDB are synchronous, so this runs via
        # asyncio.to_thread — calling it directly would block Knowledge's
        # whole event loop for the scan's duration (see main.py's module
        # docstring for why every synchronous call here goes through it).
        raw_result = await asyncio.to_thread(
            _execute_duckdb_operation, dataset_name, source_column, filter_value, operation, iceberg_config,
            aggregate_source_column=aggregate_source_column, aggregate_function=aggregate_function,
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
        "source_column": source_column,
        "dataset_name": dataset_name,
        "operation": operation,
        # Stored so `replay()` is fully self-contained from the frozen
        # plan alone — it must not need to re-derive any of this from the
        # ObjectType's *current* property mapping or ontology state,
        # which could itself have changed since this plan was first run.
        "filter_property": filter_property,
        "filter_value": filter_value,
        "group_by_property": group_by_property,
        "aggregate_property": aggregate_property,
        "aggregate_function": aggregate_function,
        "aggregate_source_column": aggregate_source_column,
        "relation_name": relation_name,
        "storage_kind": storage_kind if operation == "join" else None,
        "bridge_dataset_name": bridge_dataset_name,
        "bridge_source_column": bridge_source_column,
        "bridge_target_column": bridge_target_column,
        "bridge_dataset_version_urn": bridge_dataset_version_urn,
        "source_id_column": source_id_column,
        "target_object_type": target_object_type_name,
        "target_dataset_name": target_dataset_name,
        "target_dataset_version_urn": target_dataset_version_urn,
        "target_id_column": target_id_column,
        "source_columns": list(property_mapping.values()) if operation == "join" else None,
        "target_columns": list(target_property_mapping.values()) if operation == "join" else None,
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


async def replay(pool: asyncpg.Pool, iceberg_config: dict, *, plan_hash: str, tenant_id: str) -> dict:
    """Re-executes a
    previously-run, frozen plan against the *exact historical Iceberg
    snapshot(s)* it originally pinned (via the stored `dataset_version_urn`
    — both of them, for a `join`), not whatever those datasets look like
    now — genuinely proving reproducibility rather than trivially
    matching because nothing changed since.
    """
    row = await pool.fetchrow("SELECT plan, result, tenant_id FROM execution_run WHERE plan_hash = $1", plan_hash)
    if row is None:
        raise ValueError(f"no execution_run found for plan_hash {plan_hash!r}")
    if row["tenant_id"] != tenant_id:
        raise ValueError(f"no execution_run found for plan_hash {plan_hash!r}")
    iceberg_config = {**iceberg_config, "tenant_id": tenant_id}

    plan = json.loads(row["plan"])
    original_result = json.loads(row["result"])
    operation = plan.get("operation", "filter")  # plans frozen before this field existed default to "filter"

    dataset_version = await catalog.get_dataset_version_by_urn(pool, plan["dataset_version_urn"])
    if dataset_version is None:
        raise ValueError(f"pinned dataset_version {plan['dataset_version_urn']!r} no longer exists")

    if operation == "join":
        target_dataset_version = await catalog.get_dataset_version_by_urn(pool, plan["target_dataset_version_urn"])
        if target_dataset_version is None:
            raise ValueError(f"pinned target dataset_version {plan['target_dataset_version_urn']!r} no longer exists")
        kind = plan.get("storage_kind") or "foreign_key"
        if kind in ("join_dataset", "object_backed"):
            bridge_version_urn = plan.get("bridge_dataset_version_urn")
            if not bridge_version_urn:
                raise ValueError("bridged join plan is missing bridge_dataset_version_urn")
            bridge_dataset_version = await catalog.get_dataset_version_by_urn(pool, bridge_version_urn)
            if bridge_dataset_version is None:
                raise ValueError(f"pinned bridge dataset_version {bridge_version_urn!r} no longer exists")
            raw_result = await asyncio.to_thread(
                _execute_duckdb_bridged_join,
                plan["dataset_name"],
                plan["source_id_column"] or plan["source_column"],
                plan["source_columns"],
                plan["bridge_dataset_name"],
                plan["bridge_source_column"],
                plan["bridge_target_column"],
                plan["target_dataset_name"],
                plan["target_id_column"],
                plan["target_columns"],
                iceberg_config,
                source_snapshot_id=dataset_version["snapshot_id"],
                bridge_snapshot_id=bridge_dataset_version["snapshot_id"],
                target_snapshot_id=target_dataset_version["snapshot_id"],
            )
        else:
            raw_result = await asyncio.to_thread(
                _execute_duckdb_join,
                plan["dataset_name"], plan["source_column"], plan["source_columns"],
                plan["target_dataset_name"], plan["target_id_column"], plan["target_columns"],
                iceberg_config,
                source_snapshot_id=dataset_version["snapshot_id"],
                target_snapshot_id=target_dataset_version["snapshot_id"],
            )
    else:
        raw_result = await asyncio.to_thread(
            _execute_duckdb_operation,
            plan["dataset_name"],
            plan["source_column"],
            plan["filter_value"],
            operation,
            iceberg_config,
            snapshot_id=dataset_version["snapshot_id"],
            aggregate_source_column=plan.get("aggregate_source_column"),
            aggregate_function=plan.get("aggregate_function") or "count",
        )
    replayed_result = json.loads(json.dumps(raw_result, default=_json_default))

    return {
        "planHash": plan_hash,
        "reproducible": replayed_result == original_result,
        "result": replayed_result,
        "originalResult": original_result,
    }
