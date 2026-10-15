"""Raw zone writer — Apache Iceberg via REST catalog.

Supports table overwrite (full refresh) and append (incremental batch) write modes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError

from holon_common.iceberg_ident import NAMESPACE, iceberg_legacy_identifier, iceberg_table_identifier
_OVERWRITE_RETRIES = 4
_OVERWRITE_RETRY_DELAY_SECONDS = 1.5


@dataclass
class IcebergWriteResult:
    namespace: str
    table: str
    snapshot_id: int
    row_count: int
    location: str


def _load_catalog(catalog_uri: str, warehouse: str, s3_endpoint: str, access_key: str, secret_key: str, region: str):
    return load_catalog(
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


def _table_from_rows(rows: list[dict]) -> pa.Table:
    """Arrow table Iceberg can store.

    A column that is null on every row is typed as null, which Iceberg
    rejects. Keep it as an optional string until a real value shows up.
    """
    table = pa.Table.from_pylist(rows)
    arrays = []
    fields = []
    for column, field in zip(table.columns, table.schema):
        if pa.types.is_null(field.type):
            arrays.append(pa.nulls(len(table), type=pa.string()))
            fields.append(pa.field(field.name, pa.string(), nullable=True))
        else:
            arrays.append(column)
            fields.append(field)
    if not fields:
        raise ValueError("snapshot has no columns")
    return pa.Table.from_arrays(arrays, schema=pa.schema(fields))


def _result(table_name: str, table, snapshot, *, row_count: int) -> IcebergWriteResult:
    snapshot_id = snapshot.snapshot_id if snapshot is not None else 0
    location = table.location() if table is not None else ""
    return IcebergWriteResult(
        namespace=NAMESPACE,
        table=table_name,
        snapshot_id=snapshot_id,
        row_count=row_count,
        location=location,
    )


def write_snapshot(
    rows: list[dict],
    table_name: str,
    *,
    tenant_id: str,
    catalog_uri: str,
    warehouse: str,
    s3_endpoint: str,
    access_key: str,
    secret_key: str,
    region: str,
    mode: Literal["overwrite", "append"] = "overwrite",
) -> IcebergWriteResult:
    catalog = _load_catalog(catalog_uri, warehouse, s3_endpoint, access_key, secret_key, region)

    try:
        catalog.create_namespace(NAMESPACE)
    except NamespaceAlreadyExistsError:
        pass

    identifier = _ensure_prefixed_identifier(catalog, tenant_id, table_name)

    if not rows:
        try:
            table = catalog.load_table(identifier)
        except NoSuchTableError:
            if mode == "append":
                raise ValueError(
                    f"table {table_name!r} doesn't exist yet and this incremental batch is empty — "
                    "nothing to create it from"
                ) from None
            # Nothing was ever written. Record a successful empty sync.
            return _result(table_name, None, None, row_count=0)
        if mode == "append":
            snapshot = table.current_snapshot()
            return _result(
                table_name, table, snapshot, row_count=_total_records(snapshot, fallback=0) if snapshot is not None else 0
            )
        empty = pa.Table.from_pylist([], schema=table.schema().as_arrow())
        table.overwrite(empty)
        table.refresh()
        return _result(table_name, table, table.current_snapshot(), row_count=0)

    arrow_table = _table_from_rows(rows)
    table = catalog.create_table_if_not_exists(identifier, schema=arrow_table.schema)
    commit = table.overwrite if mode == "overwrite" else table.append

    for attempt in range(1, _OVERWRITE_RETRIES + 1):
        try:
            commit(arrow_table)
            break
        except Exception:
            # An append that timed out may already have landed. Retrying it writes the rows twice.
            if mode == "append" or attempt == _OVERWRITE_RETRIES:
                raise
            time.sleep(_OVERWRITE_RETRY_DELAY_SECONDS)
    table.refresh()

    snapshot = table.current_snapshot()
    return IcebergWriteResult(
        namespace=NAMESPACE,
        table=table_name,
        snapshot_id=snapshot.snapshot_id,
        # Total row count of current snapshot
        row_count=_total_records(snapshot, fallback=len(rows)),
        location=table.location(),
    )


def _ensure_prefixed_identifier(catalog, tenant_id: str, table_name: str) -> tuple[str, str]:
    """Rename `raw.<table>` → `raw.<tenant>__<table>` when the legacy
    unprefixed table is the only one present (in-place upgrade).
    """
    new_id = iceberg_table_identifier(tenant_id, table_name)
    try:
        catalog.load_table(new_id)
        return new_id
    except NoSuchTableError:
        pass
    legacy_id = iceberg_legacy_identifier(table_name)
    try:
        catalog.rename_table(legacy_id, new_id)
    except NoSuchTableError:
        pass
    except AttributeError:
        pass
    return new_id


def _total_records(snapshot, *, fallback: int) -> int:
    if snapshot is None or snapshot.summary is None:
        return fallback
    total = snapshot.summary.get("total-records")
    return int(total) if total is not None else fallback
