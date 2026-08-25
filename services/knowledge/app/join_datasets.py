"""Generate empty Iceberg join-table datasets for M:N RelationTypes.

Foundry's "Generate join table" creates a two-column PK bridge dataset.
Holon mirrors that in Knowledge (no Connectivity sync required): explicit
schema → create_table → empty overwrite → catalog registration.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError

from holon_common import build_urn
from holon_common.iceberg_ident import NAMESPACE, iceberg_legacy_identifier, iceberg_table_identifier
_OVERWRITE_RETRIES = 4
_OVERWRITE_RETRY_DELAY_SECONDS = 1.5
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


@dataclass
class JoinDatasetWriteResult:
    namespace: str
    table: str
    snapshot_id: int
    row_count: int
    location: str
    source_column: str
    target_column: str


def _load_catalog(*, catalog_uri: str, warehouse: str, s3_endpoint: str, access_key: str, secret_key: str, region: str):
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


def sanitize_dataset_name(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")
    if not cleaned.startswith("join_"):
        cleaned = f"join_{cleaned}"
    if not _SAFE_NAME.match(cleaned):
        raise ValueError(
            f"invalid join dataset name {name!r} — use lowercase letters, digits, underscores"
        )
    return cleaned


def create_empty_join_table(
    table_name: str,
    *,
    tenant_id: str,
    source_column: str,
    target_column: str,
    catalog_uri: str,
    warehouse: str,
    s3_endpoint: str,
    access_key: str,
    secret_key: str,
    region: str,
) -> JoinDatasetWriteResult:
    """Create (or empty-overwrite) a 2-column Iceberg join table.

    Columns are `int64` to match Holon's seeded integer PKs; overlays may
    still store string ids and coerce at resolve time.
    """
    if not source_column.strip() or not target_column.strip():
        raise ValueError("source_column and target_column are required")
    if source_column == target_column:
        raise ValueError("source_column and target_column must differ")

    table_name = sanitize_dataset_name(table_name)
    catalog = _load_catalog(
        catalog_uri=catalog_uri,
        warehouse=warehouse,
        s3_endpoint=s3_endpoint,
        access_key=access_key,
        secret_key=secret_key,
        region=region,
    )
    try:
        catalog.create_namespace(NAMESPACE)
    except NamespaceAlreadyExistsError:
        pass

    schema = pa.schema(
        [
            pa.field(source_column, pa.int64(), nullable=False),
            pa.field(target_column, pa.int64(), nullable=False),
        ]
    )
    arrow_table = pa.Table.from_pylist([], schema=schema)
    identifier = iceberg_table_identifier(tenant_id, table_name)
    try:
        catalog.load_table(identifier)
    except NoSuchTableError:
        try:
            catalog.rename_table(iceberg_legacy_identifier(table_name), identifier)
        except (NoSuchTableError, AttributeError):
            pass
    table = catalog.create_table_if_not_exists(identifier, schema=schema)

    for attempt in range(1, _OVERWRITE_RETRIES + 1):
        try:
            table.overwrite(arrow_table)
            break
        except Exception:
            if attempt == _OVERWRITE_RETRIES:
                raise
            time.sleep(_OVERWRITE_RETRY_DELAY_SECONDS)
    table.refresh()
    snapshot = table.current_snapshot()
    if snapshot is None:
        raise RuntimeError(f"Iceberg table {table_name!r} has no snapshot after empty overwrite")

    return JoinDatasetWriteResult(
        namespace=NAMESPACE,
        table=table_name,
        snapshot_id=snapshot.snapshot_id,
        row_count=0,
        location=table.location(),
        source_column=source_column,
        target_column=target_column,
    )


def catalog_payload(
    *,
    tenant_id: str,
    workspace_id: str,
    result: JoinDatasetWriteResult,
) -> dict:
    dataset_urn = build_urn(tenant_id, workspace_id, "dataset", result.table)
    dataset_version_urn = build_urn(tenant_id, workspace_id, "dataset-version", str(result.snapshot_id))
    return {
        "dataset_name": result.table,
        "dataset_urn": dataset_urn,
        "dataset_version_urn": dataset_version_urn,
        "iceberg_namespace": result.namespace,
        "iceberg_table": result.table,
        "snapshot_id": result.snapshot_id,
        "row_count": result.row_count,
        "location": result.location,
    }
