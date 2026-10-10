"""Raw zone writer — Apache Iceberg via REST catalog.

Supports table overwrite (full refresh) and append (incremental batch) write modes.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Literal, Optional

import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import (
    CommitFailedException,
    CommitStateUnknownException,
    NamespaceAlreadyExistsError,
    NoSuchTableError,
)

from holon_common.iceberg_ident import NAMESPACE, iceberg_legacy_identifier, iceberg_table_identifier
_OVERWRITE_RETRIES = 4
_OVERWRITE_RETRY_DELAY_SECONDS = 1.5
_SYNC_ID_PROPERTY = "holon.sync-id"


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


class SnapshotWriter:
    """Writes one sync as a stream of row batches, made visible by a single commit.

    Every batch becomes data files inside one Iceberg transaction, so memory is bounded
    by the batch and readers never see a partial sync. Columns that are null throughout
    a batch are left out until a batch carries a value for them (Iceberg v2 has no null
    type). Each snapshot carries the sync's id, which tells an ambiguous failed commit
    apart from one that actually landed.
    """

    def __init__(
        self,
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
    ) -> None:
        self._table_name = table_name
        self._tenant_id = tenant_id
        self._catalog_config = {
            "catalog_uri": catalog_uri,
            "warehouse": warehouse,
            "s3_endpoint": s3_endpoint,
            "access_key": access_key,
            "secret_key": secret_key,
            "region": region,
        }
        self._mode = mode
        self._sync_id = uuid.uuid4().hex
        self._catalog = None
        self._identifier: Optional[tuple[str, str]] = None
        self._txn = None
        self._wrote_first = False
        self.rows_written = 0

    def add(self, rows: list[dict]) -> None:
        if not rows:
            return
        inferred = _without_null_fields(pa.Table.from_pylist(rows).schema)
        if self._txn is None:
            self._open(inferred)
        self._add_new_fields(inferred)
        batch = pa.Table.from_pylist(rows, schema=self._txn.table_metadata.schema().as_arrow())
        properties = {_SYNC_ID_PROPERTY: self._sync_id}
        if self._mode == "overwrite" and not self._wrote_first:
            self._txn.overwrite(batch, snapshot_properties=properties)
        else:
            self._txn.append(batch, snapshot_properties=properties)
        self._wrote_first = True
        self.rows_written += len(rows)

    def commit(self) -> IcebergWriteResult:
        if self._txn is None:
            return write_snapshot(
                [], self._table_name, tenant_id=self._tenant_id, mode=self._mode, **self._catalog_config
            )
        for attempt in range(1, _OVERWRITE_RETRIES + 1):
            try:
                self._txn.commit_transaction()
                break
            except Exception as exc:
                if self._landed():
                    break
                if isinstance(exc, CommitFailedException) or attempt == _OVERWRITE_RETRIES:
                    raise
                time.sleep(_OVERWRITE_RETRY_DELAY_SECONDS)
        table = self._catalog.load_table(self._identifier)
        snapshot = table.current_snapshot()
        return IcebergWriteResult(
            namespace=NAMESPACE,
            table=self._table_name,
            snapshot_id=snapshot.snapshot_id,
            row_count=_total_records(snapshot, fallback=self.rows_written),
            location=table.location(),
        )

    def _open(self, schema: pa.Schema) -> None:
        config = self._catalog_config
        self._catalog = _load_catalog(
            config["catalog_uri"], config["warehouse"], config["s3_endpoint"],
            config["access_key"], config["secret_key"], config["region"],
        )
        try:
            self._catalog.create_namespace(NAMESPACE)
        except NamespaceAlreadyExistsError:
            pass
        self._identifier = _ensure_prefixed_identifier(self._catalog, self._tenant_id, self._table_name)
        table = self._catalog.create_table_if_not_exists(self._identifier, schema=schema)
        self._txn = table.transaction()

    def _add_new_fields(self, schema: pa.Schema) -> None:
        known = set(self._txn.table_metadata.schema().as_arrow().names)
        new_fields = [field for field in schema if field.name not in known]
        if new_fields:
            with self._txn.update_schema() as update:
                update.union_by_name(pa.schema(new_fields))

    def _landed(self) -> bool:
        try:
            table = self._catalog.load_table(self._identifier)
        except Exception:
            return False
        return any(
            snapshot.summary is not None and snapshot.summary.get(_SYNC_ID_PROPERTY) == self._sync_id
            for snapshot in table.snapshots()
        )


def _without_null_fields(schema: pa.Schema) -> pa.Schema:
    return pa.schema([field for field in schema if not pa.types.is_null(field.type)])


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
    total = snapshot.summary.get("total-records") if snapshot.summary is not None else None
    return int(total) if total is not None else fallback
