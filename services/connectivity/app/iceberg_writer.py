"""Raw zone writer — Apache Iceberg via REST catalog.

Supports table overwrite (full refresh) and append (incremental batch) write modes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import CommitStateUnknownException, NamespaceAlreadyExistsError, NoSuchTableError

NAMESPACE = "raw"
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


def write_snapshot(
    rows: list[dict],
    table_name: str,
    *,
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

    identifier = (NAMESPACE, table_name)

    if not rows and mode == "append":
        # Empty batch with append mode: return current table snapshot state unchanged
        try:
            table = catalog.load_table(identifier)
        except NoSuchTableError:
            raise ValueError(
                f"table {table_name!r} doesn't exist yet and this incremental batch is empty — "
                "nothing to create it from"
            ) from None
        snapshot = table.current_snapshot()
        return IcebergWriteResult(
            namespace=NAMESPACE, table=table_name, snapshot_id=snapshot.snapshot_id,
            row_count=_total_records(snapshot, fallback=0), location=table.location(),
        )

    arrow_table = pa.Table.from_pylist(rows)
    table = catalog.create_table_if_not_exists(identifier, schema=arrow_table.schema)
    commit = table.overwrite if mode == "overwrite" else table.append

    for attempt in range(1, _OVERWRITE_RETRIES + 1):
        try:
            commit(arrow_table)
            break
        except CommitStateUnknownException:
            # Append mode: do not retry CommitStateUnknownException to avoid duplicate rows
            if mode == "append":
                raise
            if attempt == _OVERWRITE_RETRIES:
                raise
            time.sleep(_OVERWRITE_RETRY_DELAY_SECONDS)
        except Exception:
            if attempt == _OVERWRITE_RETRIES:
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


def _total_records(snapshot, *, fallback: int) -> int:
    total = snapshot.summary.get("total-records") if snapshot.summary is not None else None
    return int(total) if total is not None else fallback
