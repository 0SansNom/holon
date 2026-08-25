"""Iceberg and DuckDB instance resolution with push-down filters."""

from __future__ import annotations

import time
from typing import Optional

import duckdb
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.expressions import EqualTo

from holon_common.iceberg_ident import iceberg_read_identifiers
from holon_common.sql_ident import quote_identifier

_LOAD_TABLE_RETRIES = 4
_LOAD_TABLE_RETRY_DELAY_SECONDS = 1.5


def _load_table(table_name: str, *, tenant_id: str, catalog_uri: str, warehouse: str, s3_endpoint: str, access_key: str, secret_key: str, region: str):
    catalog = load_catalog(
        "holon",
        **{
            "type": "rest",
            "uri": catalog_uri,
            "warehouse": warehouse,
            "s3.endpoint": s3_endpoint,
            "s3.access-key-id": access_key,
            "s3.secret-access-key": secret_key,
            "s3.region": region,
            "s3.path-style-access": "true",
        },
    )
    last_missing = None
    for identifier in iceberg_read_identifiers(tenant_id, table_name):
        for attempt in range(1, _LOAD_TABLE_RETRIES + 1):
            try:
                return catalog.load_table(identifier)
            except NoSuchTableError as exc:
                last_missing = exc
                break
            except Exception:
                if attempt == _LOAD_TABLE_RETRIES:
                    raise
                time.sleep(_LOAD_TABLE_RETRY_DELAY_SECONDS)
    if last_missing is not None:
        raise last_missing
    raise NoSuchTableError(table_name)


def scan_at(table_name: str, *, snapshot_id: Optional[int] = None, **iceberg_config):
    """Scan an Iceberg table at a specific snapshot ID (or latest if None)."""
    table = _load_table(table_name, **iceberg_config)
    return table.scan(snapshot_id=snapshot_id) if snapshot_id is not None else table.scan()


def _scan_arrow(table, *, equal_column: Optional[str] = None, equal_value=None):
    if equal_column is not None and equal_value is not None:
        return table.scan(row_filter=EqualTo(equal_column, equal_value)).to_arrow()
    return table.scan().to_arrow()


def _duck_select(arrow_table, sql: str, params: Optional[list] = None) -> list[dict]:
    con = duckdb.connect()
    con.register("t", arrow_table)
    rows = con.execute(sql, params or []).fetch_arrow_table()
    return rows.to_pylist()


def fetch_generic(
    dataset_name: str, *, id_value: Optional[str] = None,
    filter_column: Optional[str] = None, filter_value=None, **iceberg_config,
) -> list[dict]:
    """Generic fetch for dynamic dataset names and arbitrary filter conditions."""
    table = _load_table(dataset_name, **iceberg_config)
    if id_value is not None:
        try:
            typed_id: object = int(id_value)
        except (TypeError, ValueError):
            typed_id = id_value
        arrow_table = _scan_arrow(table, equal_column="id", equal_value=typed_id)
        return _duck_select(arrow_table, "SELECT * FROM t WHERE id = ?", [typed_id])
    if filter_column is not None:
        arrow_table = _scan_arrow(table, equal_column=filter_column, equal_value=filter_value)
        if filter_column not in arrow_table.column_names and len(arrow_table) == 0:
            probe = _scan_arrow(table)
            if filter_column not in probe.column_names:
                return []
            arrow_table = probe
        return _duck_select(arrow_table, f"SELECT * FROM t WHERE {quote_identifier(filter_column)} = ?", [filter_value])
    arrow_table = _scan_arrow(table)
    order_col = "id" if "id" in arrow_table.column_names else (
        arrow_table.column_names[0] if arrow_table.column_names else None
    )
    if order_col:
        return _duck_select(arrow_table, f"SELECT * FROM t ORDER BY {quote_identifier(order_col)}")
    return _duck_select(arrow_table, "SELECT * FROM t")


def dataset_schema_and_stats(dataset_name: str, **iceberg_config) -> dict:
    """The Iceberg table's own declared schema (field name/type/required —
    not inferred from one sample row, the actual committed schema) plus
    per-column stats computed over every row currently in the table.
    Heavier than `fetch_generic`'s single-row preview (a full scan), so
    this is its own call, not folded into `preview_dataset` — callers
    fetch it only when they actually want to look, not on every render.

    One column's stats failing (MIN/MAX on a struct/list-typed column,
    which DuckDB can't order) degrades that column to `null`s rather
    than failing the whole response — every other column's stats are
    still real numbers, not "unavailable" because of one odd one.
    """
    table = _load_table(dataset_name, **iceberg_config)
    fields = [
        {"name": f.name, "type": str(f.field_type), "required": bool(f.required)}
        for f in table.schema().fields
    ]
    arrow_table = _scan_arrow(table)
    con = duckdb.connect()
    con.register("t", arrow_table)
    row_count = con.execute("SELECT COUNT(*) FROM t").fetchone()[0]

    columns = []
    for field in fields:
        name = field["name"]
        non_null_count = distinct_count = min_value = max_value = None
        try:
            ident = quote_identifier(name)
            non_null_count, distinct_count, min_value, max_value = con.execute(
                f"SELECT COUNT({ident}), COUNT(DISTINCT {ident}), MIN({ident})::VARCHAR, MAX({ident})::VARCHAR FROM t"
            ).fetchone()
        except (duckdb.Error, ValueError):
            pass
        columns.append({
            **field,
            "null_count": (row_count - non_null_count) if non_null_count is not None else None,
            "distinct_count": distinct_count,
            "min": min_value,
            "max": max_value,
        })
    return {"row_count": row_count, "columns": columns}
