"""No-code object storage source registry for S3-compatible (AWS, MinIO, IBM COS), Azure Blob, and GCS.

IBM Cloud Object Storage uses kind=s3 with an HMAC key pair and a regional
endpoint such as ``https://s3.us-south.cloud-object-storage.appdomain.cloud``
(path-style usually off — virtual-hosted addressing).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Awaitable, Callable, Optional
from urllib.parse import urlsplit

import asyncpg
import pyarrow.csv as pacsv
import pyarrow.fs as pafs
import pyarrow.json as pajson
import pyarrow.parquet as papq
from pyarrow.lib import ArrowException

from app.file_cursor import info_mtime_ns, select_files
from app import source_registry_base
from app.source_registry_base import (
    ConnectionConflictError as ConnectionConflictError,
    ConnectionInUseError,
    SourceConflictError as SourceConflictError,
    SourceConfigError,
    SourceFetchError,
    assert_dataset_available,
    make_column_cursor_commit,
    resolve_source_secret,
)

from holon_common.connector_safety import (
    ConnectorSafetyError,
    assert_connector_host,
    pin_object_endpoint,
    assert_connector_secret_ref,
    assert_destination_change_requires_secret,
    assert_no_inline_connector_secret,
    assert_production_requires_secret_ref,
    connector_secret,
)

logger = logging.getLogger(__name__)

_FORMATS = frozenset({"csv", "ndjson", "parquet"})
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
# Object keys / prefixes: any printable segments (Hive partitions like
# `year=2024/`, spaces, `+` are all common in real buckets), no empty or
# `..` segments, no control characters; trailing slash OK for prefixes.
_OBJECT_KEY_RE = re.compile(r"^/?[^/\x00-\x1f\x7f\\]+(/[^/\x00-\x1f\x7f\\]+)*/?$")
_CONNECTION_KINDS = frozenset({"s3", "azure", "gcs"})
_DEFAULT_GCS_ENDPOINT = "https://storage.googleapis.com"
# Soft check that secret looks like a Google service-account JSON key
# (CData OAuthJWTCertType=GOOGLEJSON / "JSON credentials").
_GCS_JSON_MARKERS = ("private_key", "client_email", "type")

_PUBLIC_CONNECTION_COLUMNS = (
    "tenant_id, name, kind, endpoint, region, access_key_id, path_style, "
    "(secret_access_key IS NOT NULL OR secret_ref IS NOT NULL) AS has_secret_access_key, "
    "created_by_urn, created_at"
)

_PUBLIC_SOURCE_COLUMNS = (
    "tenant_id, name, workspace_id, connection_name, bucket, object_key, key_prefix, format, "
    "incremental, last_synced_key, schedule_interval_minutes, status, created_by_urn, created_at"
)




def _require_bucket(bucket: str) -> None:
    if not bucket or not _BUCKET_RE.match(bucket):
        raise SourceConfigError(
            f"invalid bucket {bucket!r} — must be 3-63 chars, lowercase letters/digits/dot/hyphen"
        )


def _require_object_key(path: str, *, what: str) -> None:
    if not path or not _OBJECT_KEY_RE.match(path) or ".." in path.split("/"):
        raise SourceConfigError(
            f"invalid {what} {path!r} — use a plain object key or prefix "
            "(e.g. 'landing/data.csv' or 'landing/'), no '..'"
        )


def _default_azure_endpoint(account_name: str) -> str:
    return f"https://{account_name}.blob.core.windows.net"


def _default_gcs_endpoint() -> str:
    return _DEFAULT_GCS_ENDPOINT


def _validate_gcs_service_account_json(raw: Optional[str]) -> None:
    """Reject obviously non-JSON secrets early (full auth is checked at fetch)."""
    if raw is None or not raw.strip():
        return
    stripped = raw.strip()
    if not stripped.startswith("{"):
        raise SourceConfigError(
            "GCS secret_access_key must be a Google service account JSON key "
            "(or use secret_ref pointing at one) — see ProjectId + GOOGLEJSON in CData docs"
        )
    try:
        import json

        info = json.loads(stripped)
    except ValueError as exc:
        raise SourceConfigError(f"GCS service account JSON is not valid JSON: {exc}") from exc
    if not isinstance(info, dict) or not all(k in info for k in _GCS_JSON_MARKERS):
        raise SourceConfigError(
            "GCS service account JSON must include type, client_email, and private_key"
        )


async def register_connection(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    access_key_id: str,
    created_by_urn: str,
    kind: str = "s3",
    endpoint: Optional[str] = None,
    region: str = "us-east-1",
    path_style: bool = True,
    secret_access_key: Optional[str] = None,
    secret_ref: Optional[str] = None,
) -> dict:
    """Register or update an object storage connection credential.

    For kind='azure', access_key_id/secret_access_key hold the storage
    account name/key rather than S3 credentials, and endpoint defaults to
    the account's public Blob endpoint when omitted.

    For kind='gcs', access_key_id is the GCP Project Id and
    secret_access_key/secret_ref hold the service account JSON key
    ("JSON credentials" / CData OAuthJWTCertType=GOOGLEJSON).
    Endpoint defaults to https://storage.googleapis.com; region is the
    default bucket location (e.g. US).
    """
    if kind not in _CONNECTION_KINDS:
        raise SourceConfigError(f"kind must be one of {sorted(_CONNECTION_KINDS)}")
    if not endpoint:
        if kind == "azure":
            endpoint = _default_azure_endpoint(access_key_id)
        elif kind == "gcs":
            endpoint = _default_gcs_endpoint()
        else:
            raise SourceConfigError("endpoint is required for kind='s3'")
    if kind == "gcs":
        if not access_key_id.strip():
            raise SourceConfigError("access_key_id (GCP Project Id) is required for kind='gcs'")
        # GCS does not use S3 path-style addressing.
        path_style = False
        if region == "us-east-1":
            region = "US"
        _validate_gcs_service_account_json(secret_access_key)
    hostname = urlsplit(endpoint if "://" in endpoint else f"//{endpoint}").hostname
    existing = await pool.fetchrow(
        "SELECT kind, endpoint, region, access_key_id, path_style, secret_access_key, secret_ref "
        "FROM object_connection WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )
    is_update = existing is not None
    try:
        assert_connector_host(hostname or "")
        assert_connector_secret_ref(secret_ref, tenant_id=tenant_id)
        assert_no_inline_connector_secret(secret_access_key, field="secret_access_key")
        if existing is not None:
            destination_changed = (
                existing["kind"] != kind
                or existing["endpoint"] != endpoint
                or existing["access_key_id"] != access_key_id
            )
            assert_destination_change_requires_secret(
                is_update=True,
                destination_changed=destination_changed,
                secret_provided=secret_access_key is not None or secret_ref is not None,
            )
    except ConnectorSafetyError as exc:
        raise SourceConfigError(str(exc)) from exc
    if secret_access_key is None and secret_ref is None and existing is not None:
        secret_access_key, secret_ref = existing["secret_access_key"], existing["secret_ref"]
    try:
        assert_production_requires_secret_ref(secret_ref, is_update=is_update)
    except ConnectorSafetyError as exc:
        raise SourceConfigError(str(exc)) from exc
    if kind == "gcs" and secret_access_key:
        _validate_gcs_service_account_json(secret_access_key)

    await pool.execute(
        """
        INSERT INTO object_connection
            (tenant_id, name, kind, endpoint, region, access_key_id, secret_access_key, secret_ref, path_style, created_by_urn)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            kind = EXCLUDED.kind,
            endpoint = EXCLUDED.endpoint,
            region = EXCLUDED.region,
            access_key_id = EXCLUDED.access_key_id,
            secret_access_key = EXCLUDED.secret_access_key,
            secret_ref = EXCLUDED.secret_ref,
            path_style = EXCLUDED.path_style
        """,
        tenant_id, name, kind, endpoint, region, access_key_id, secret_access_key, secret_ref, path_style, created_by_urn,
    )
    return await get_connection(pool, tenant_id, name)


async def get_connection(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        f"SELECT {_PUBLIC_CONNECTION_COLUMNS} FROM object_connection WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )
    return None if row is None else dict(row)


async def list_connections(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        f"SELECT {_PUBLIC_CONNECTION_COLUMNS} FROM object_connection WHERE tenant_id = $1 ORDER BY name", tenant_id
    )
    return [dict(row) for row in rows]


async def delete_connection(pool: asyncpg.Pool, tenant_id: str, name: str) -> None:
    await source_registry_base.delete_connection_if_unused(
        pool,
        connection_table="object_connection",
        source_table="object_source",
        tenant_id=tenant_id,
        name=name,
    )

async def register_source(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    workspace_id: str,
    connection_name: str,
    bucket: str,
    format: str,
    created_by_urn: str,
    object_key: Optional[str] = None,
    key_prefix: Optional[str] = None,
    incremental: bool = False,
    schedule_interval_minutes: Optional[int] = None,
    reserved_dataset_names: frozenset[str] = frozenset(),
) -> dict:
    """Verify dataset name availability and validate object source parameters."""
    if bool(object_key) == bool(key_prefix):
        raise SourceConfigError("exactly one of object_key or key_prefix must be set")
    if format not in _FORMATS:
        raise SourceConfigError(f"format must be one of {sorted(_FORMATS)}")
    _require_bucket(bucket)
    if object_key:
        _require_object_key(object_key, what="object_key")
    if key_prefix:
        _require_object_key(key_prefix, what="key_prefix")
    if incremental and object_key:
        raise SourceConfigError("incremental only applies to key_prefix sources, not a single object_key")
    if await get_connection(pool, tenant_id, connection_name) is None:
        raise SourceConfigError(f"unknown connection: {connection_name!r}")
    if schedule_interval_minutes is not None and schedule_interval_minutes <= 0:
        raise SourceConfigError("schedule_interval_minutes must be a positive number of minutes")

    await assert_dataset_available(
        pool,
        tenant_id=tenant_id,
        name=name,
        reserved_dataset_names=reserved_dataset_names,
        exclude_table="object_source",
    )

    await pool.execute(
        """
        INSERT INTO object_source
            (tenant_id, name, workspace_id, connection_name, bucket, object_key, key_prefix, format,
             incremental, schedule_interval_minutes, status, created_by_urn)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'active', $11)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            workspace_id = EXCLUDED.workspace_id,
            connection_name = EXCLUDED.connection_name,
            bucket = EXCLUDED.bucket,
            object_key = EXCLUDED.object_key,
            key_prefix = EXCLUDED.key_prefix,
            format = EXCLUDED.format,
            incremental = EXCLUDED.incremental,
            schedule_interval_minutes = EXCLUDED.schedule_interval_minutes,
            -- last_synced_key deliberately absent: resume state computed
            -- by fetch_for_dataset, not a form field.
            status = 'active'
        """,
        tenant_id, name, workspace_id, connection_name, bucket, object_key, key_prefix, format,
        incremental, schedule_interval_minutes, created_by_urn,
    )
    return await get_source(pool, tenant_id, name)


async def list_scheduled_sources(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    return await source_registry_base.list_scheduled_sources_rows(pool, table="object_source", tenant_id=tenant_id)

async def list_all_scheduled_sources(pool: asyncpg.Pool) -> list[dict]:
    return await source_registry_base.list_all_scheduled_sources_rows(pool, table="object_source")

async def set_source_status(pool: asyncpg.Pool, tenant_id: str, name: str, status: str) -> Optional[dict]:
    return await source_registry_base.set_source_status_row(
        pool, table="object_source", public_columns=_PUBLIC_SOURCE_COLUMNS, tenant_id=tenant_id, name=name, status=status
    )

async def delete_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> None:
    await source_registry_base.delete_source_row(pool, table="object_source", tenant_id=tenant_id, name=name)

async def get_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    return await source_registry_base.get_source_row(
        pool, table="object_source", public_columns=_PUBLIC_SOURCE_COLUMNS, tenant_id=tenant_id, name=name
    )

async def list_sources(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    return await source_registry_base.list_sources_for_tenant(
        pool, table="object_source", public_columns=_PUBLIC_SOURCE_COLUMNS, tenant_id=tenant_id
    )

async def is_registered(pool: asyncpg.Pool, tenant_id: str, name: str) -> bool:
    return await source_registry_base.is_source_active(pool, table="object_source", tenant_id=tenant_id, name=name)

from app.object_source_fetch import (  # noqa: E402,F401
    _build_filesystem,
    _build_gcs_filesystem,
    _fetch_sync,
    _format_suffix,
    _matches_format,
    _read_table,
    fetch_for_dataset,
)
