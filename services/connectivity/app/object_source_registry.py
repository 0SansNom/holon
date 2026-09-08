"""No-code object storage source registry for S3-compatible and Azure Blob endpoints."""

from __future__ import annotations

import asyncio
import re
from typing import Optional
from urllib.parse import urlsplit

import asyncpg
import pyarrow.csv as pacsv
import pyarrow.fs as pafs
import pyarrow.json as pajson
import pyarrow.parquet as papq
from pyarrow.lib import ArrowException

from holon_common.connector_safety import (
    ConnectorSafetyError,
    assert_connector_host,
    assert_connector_secret_ref,
    assert_no_inline_connector_secret,
    assert_production_requires_secret_ref,
)
from holon_common.secrets import resolve_optional

DDL = """
CREATE TABLE IF NOT EXISTS object_connection (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT 'us-east-1',
    access_key_id TEXT NOT NULL,
    secret_access_key TEXT,
    secret_ref TEXT,
    path_style BOOLEAN NOT NULL DEFAULT true,
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

-- 's3' (endpoint/region/path_style, S3-compatible) or 'azure' (Blob Storage:
-- access_key_id/secret_access_key double as account name/account key).
ALTER TABLE object_connection ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 's3';

CREATE TABLE IF NOT EXISTS object_source (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    connection_name TEXT NOT NULL,
    bucket TEXT NOT NULL,
    object_key TEXT,
    key_prefix TEXT,
    format TEXT NOT NULL,
    incremental BOOLEAN NOT NULL DEFAULT false,
    last_synced_key TEXT,
    schedule_interval_minutes INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);
"""

_FORMATS = frozenset({"csv", "ndjson", "parquet"})
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_CONNECTION_KINDS = frozenset({"s3", "azure"})

_PUBLIC_CONNECTION_COLUMNS = (
    "tenant_id, name, kind, endpoint, region, access_key_id, path_style, "
    "(secret_access_key IS NOT NULL OR secret_ref IS NOT NULL) AS has_secret_access_key, "
    "created_by_urn, created_at"
)

_PUBLIC_SOURCE_COLUMNS = (
    "tenant_id, name, workspace_id, connection_name, bucket, object_key, key_prefix, format, "
    "incremental, last_synced_key, schedule_interval_minutes, status, created_by_urn, created_at"
)


class SourceConflictError(ValueError):
    pass


class SourceConfigError(ValueError):
    pass


class SourceFetchError(ValueError):
    pass


class ConnectionConflictError(ValueError):
    pass


class ConnectionInUseError(ValueError):
    pass


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


def _require_bucket(bucket: str) -> None:
    if not bucket or not _BUCKET_RE.match(bucket):
        raise SourceConfigError(
            f"invalid bucket {bucket!r} — must be 3-63 chars, lowercase letters/digits/dot/hyphen"
        )


def _default_azure_endpoint(account_name: str) -> str:
    return f"https://{account_name}.blob.core.windows.net"


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
    """
    if kind not in _CONNECTION_KINDS:
        raise SourceConfigError(f"kind must be one of {sorted(_CONNECTION_KINDS)}")
    if not endpoint:
        if kind == "azure":
            endpoint = _default_azure_endpoint(access_key_id)
        else:
            raise SourceConfigError("endpoint is required for kind='s3'")
    hostname = urlsplit(endpoint if "://" in endpoint else f"//{endpoint}").hostname
    existing = await pool.fetchrow(
        "SELECT secret_access_key, secret_ref FROM object_connection WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )
    is_update = existing is not None
    try:
        assert_connector_host(hostname or "")
        assert_connector_secret_ref(secret_ref, tenant_id=tenant_id)
        assert_no_inline_connector_secret(secret_access_key, field="secret_access_key")
    except ConnectorSafetyError as exc:
        raise SourceConfigError(str(exc)) from exc
    if secret_access_key is None and secret_ref is None and existing is not None:
        secret_access_key, secret_ref = existing["secret_access_key"], existing["secret_ref"]
    try:
        assert_production_requires_secret_ref(secret_ref, is_update=is_update)
    except ConnectorSafetyError as exc:
        raise SourceConfigError(str(exc)) from exc

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
    in_use = await pool.fetch(
        "SELECT name FROM object_source WHERE tenant_id = $1 AND connection_name = $2", tenant_id, name
    )
    if in_use:
        source_names = [row["name"] for row in in_use]
        raise ConnectionInUseError(
            f"connection {name!r} is still used by source(s) {source_names} — repoint or delete them first"
        )
    await pool.execute("DELETE FROM object_connection WHERE tenant_id = $1 AND name = $2", tenant_id, name)


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
    if incremental and object_key:
        raise SourceConfigError("incremental only applies to key_prefix sources, not a single object_key")
    if await get_connection(pool, tenant_id, connection_name) is None:
        raise SourceConfigError(f"unknown connection: {connection_name!r}")
    if schedule_interval_minutes is not None and schedule_interval_minutes <= 0:
        raise SourceConfigError("schedule_interval_minutes must be a positive number of minutes")

    if name in reserved_dataset_names:
        raise SourceConflictError(f"dataset {name!r} is reserved")

    conflicting_plugin = await pool.fetchval(
        """
        SELECT name FROM plugin_registration
        WHERE manifest->>'dataset_name' = $1
          AND status = 'active'
          AND (tenant_id IS NULL OR tenant_id = $2)
        """,
        name, tenant_id,
    )
    if conflicting_plugin is not None:
        raise SourceConflictError(f"dataset {name!r} is already claimed by active plugin {conflicting_plugin!r}")

    conflicting_rest_source = await pool.fetchval(
        "SELECT name FROM generic_rest_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    )
    if conflicting_rest_source is not None:
        raise SourceConflictError(f"dataset {name!r} is already claimed by active REST source {conflicting_rest_source!r}")

    conflicting_sql_source = await pool.fetchval(
        "SELECT name FROM sql_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    )
    if conflicting_sql_source is not None:
        raise SourceConflictError(f"dataset {name!r} is already claimed by active SQL source {conflicting_sql_source!r}")

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
    rows = await pool.fetch(
        "SELECT name, schedule_interval_minutes FROM object_source "
        "WHERE tenant_id = $1 AND status = 'active' AND schedule_interval_minutes IS NOT NULL",
        tenant_id,
    )
    return [dict(row) for row in rows]


async def list_all_scheduled_sources(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch(
        "SELECT tenant_id, name, workspace_id, schedule_interval_minutes FROM object_source "
        "WHERE status = 'active' AND schedule_interval_minutes IS NOT NULL"
    )
    return [dict(row) for row in rows]


async def set_source_status(pool: asyncpg.Pool, tenant_id: str, name: str, status: str) -> Optional[dict]:
    await pool.execute(
        "UPDATE object_source SET status = $1 WHERE tenant_id = $2 AND name = $3", status, tenant_id, name
    )
    return await get_source(pool, tenant_id, name)


async def delete_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> None:
    await pool.execute("DELETE FROM object_source WHERE tenant_id = $1 AND name = $2", tenant_id, name)


async def get_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        f"SELECT {_PUBLIC_SOURCE_COLUMNS} FROM object_source WHERE tenant_id = $1 AND name = $2", tenant_id, name
    )
    return None if row is None else dict(row)


async def list_sources(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        f"SELECT {_PUBLIC_SOURCE_COLUMNS} FROM object_source WHERE tenant_id = $1 ORDER BY name", tenant_id
    )
    return [dict(row) for row in rows]


async def is_registered(pool: asyncpg.Pool, tenant_id: str, name: str) -> bool:
    return await pool.fetchval(
        "SELECT true FROM object_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    ) or False


def _build_filesystem(
    *, kind: str, endpoint: str, access_key_id: str, secret_access_key: str, region: str, path_style: bool
) -> pafs.FileSystem:
    if kind == "azure":
        # access_key_id/secret_access_key double as the storage account
        # name/key here; container addressing (container/blob) mirrors S3's
        # bucket/key, so the read/list code below is shared as-is.
        return pafs.AzureFileSystem(account_name=access_key_id, account_key=secret_access_key)
    parsed = urlsplit(endpoint if "://" in endpoint else f"//{endpoint}")
    scheme = parsed.scheme or "https"
    endpoint_override = parsed.netloc or parsed.path
    return pafs.S3FileSystem(
        access_key=access_key_id,
        secret_key=secret_access_key,
        endpoint_override=endpoint_override,
        scheme=scheme,
        region=region,
        force_virtual_addressing=not path_style,
    )


def _read_table(fs: pafs.FileSystem, path: str, format: str):
    with fs.open_input_stream(path) as stream:
        if format == "csv":
            return pacsv.read_csv(stream)
        if format == "ndjson":
            return pajson.read_json(stream)
        return papq.read_table(stream)


def _fetch_sync(
    *,
    kind: str,
    endpoint: str,
    access_key_id: str,
    secret_access_key: str,
    region: str,
    path_style: bool,
    bucket: str,
    object_key: Optional[str],
    key_prefix: Optional[str],
    format: str,
    incremental: bool,
    last_synced_key: Optional[str],
) -> tuple[list[dict], Optional[str]]:
    fs = _build_filesystem(
        kind=kind,
        endpoint=endpoint,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        region=region,
        path_style=path_style,
    )

    if object_key:
        table = _read_table(fs, f"{bucket}/{object_key}", format)
        return table.to_pylist(), None

    selector = pafs.FileSelector(f"{bucket}/{key_prefix}", recursive=True)
    infos = fs.get_file_info(selector)
    keys = sorted(
        info.path[len(bucket) + 1:]
        for info in infos
        if info.type == pafs.FileType.File
    )
    if incremental and last_synced_key:
        keys = [key for key in keys if key > last_synced_key]

    rows: list[dict] = []
    new_cursor = last_synced_key
    for key in keys:
        table = _read_table(fs, f"{bucket}/{key}", format)
        rows.extend(table.to_pylist())
        new_cursor = key

    return rows, (new_cursor if incremental else None)


async def fetch_for_dataset(pool: asyncpg.Pool, tenant_id: str, name: str) -> list[dict]:
    row = await pool.fetchrow(
        "SELECT connection_name, bucket, object_key, key_prefix, format, incremental, last_synced_key "
        "FROM object_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    )
    if row is None:
        raise SourceFetchError(f"no active object source registered as {name!r}")

    connection = await pool.fetchrow(
        "SELECT kind, endpoint, region, access_key_id, secret_access_key, secret_ref, path_style "
        "FROM object_connection WHERE tenant_id = $1 AND name = $2",
        tenant_id, row["connection_name"],
    )
    if connection is None:
        raise SourceFetchError(f"source {name!r} references connection {row['connection_name']!r}, which no longer exists")

    hostname = urlsplit(
        connection["endpoint"] if "://" in connection["endpoint"] else f"//{connection['endpoint']}"
    ).hostname
    try:
        assert_connector_host(hostname or "")
    except ConnectorSafetyError as exc:
        raise SourceFetchError(str(exc)) from exc
    secret_access_key = resolve_optional(connection["secret_ref"]) or connection["secret_access_key"]

    try:
        rows, new_cursor = await asyncio.to_thread(
            _fetch_sync,
            kind=connection["kind"],
            endpoint=connection["endpoint"],
            access_key_id=connection["access_key_id"],
            secret_access_key=secret_access_key,
            region=connection["region"],
            path_style=connection["path_style"],
            bucket=row["bucket"],
            object_key=row["object_key"],
            key_prefix=row["key_prefix"],
            format=row["format"],
            incremental=row["incremental"],
            last_synced_key=row["last_synced_key"],
        )
    except (OSError, ValueError, ArrowException) as exc:
        raise SourceFetchError(f"could not read source {name!r}: {exc}") from exc

    if new_cursor is not None and new_cursor != row["last_synced_key"]:
        await pool.execute(
            "UPDATE object_source SET last_synced_key = $1 WHERE tenant_id = $2 AND name = $3",
            new_cursor, tenant_id, name,
        )

    return rows
