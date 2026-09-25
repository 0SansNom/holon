"""No-code object storage source registry for S3-compatible, Azure Blob, and GCS."""

from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable, Optional
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

_FORMATS = frozenset({"csv", "ndjson", "parquet"})
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
# Object keys / prefixes: any printable segments (Hive partitions like
# `year=2024/`, spaces, `+` are all common in real buckets), no empty or
# `..` segments, no control characters; trailing slash OK for prefixes.
_OBJECT_KEY_RE = re.compile(r"^/?[^/\x00-\x1f\x7f\\]+(/[^/\x00-\x1f\x7f\\]+)*/?$")
_CONNECTION_KINDS = frozenset({"s3", "azure", "gcs"})
_DEFAULT_GCS_ENDPOINT = "https://storage.googleapis.com"
# Soft check that secret looks like a Google service-account JSON key
# (CData OAuthJWTCertType=GOOGLEJSON / Foundry "JSON credentials").
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
            "(or use secret_ref pointing at one) — see ProjectId + GOOGLEJSON in CData/Foundry docs"
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
    (Foundry "JSON credentials" / CData OAuthJWTCertType=GOOGLEJSON).
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

    conflicting_sftp_source = await pool.fetchval(
        "SELECT name FROM sftp_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    )
    if conflicting_sftp_source is not None:
        raise SourceConflictError(f"dataset {name!r} is already claimed by active SFTP source {conflicting_sftp_source!r}")

    conflicting_sf_source = await pool.fetchval(
        "SELECT name FROM salesforce_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    )
    if conflicting_sf_source is not None:
        raise SourceConflictError(
            f"dataset {name!r} is already claimed by active Salesforce source {conflicting_sf_source!r}"
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


def _build_gcs_filesystem(*, project_id: str, service_account_json: str, location: str) -> pafs.FileSystem:
    """Native GCS via PyArrow, authenticated with a service-account JSON key.

    Mirrors Foundry "JSON credentials" / CData AuthScheme=OAuthJWT +
    OAuthJWTCertType=GOOGLEJSON: mint an access token from the key, then
    hand it to GcsFileSystem (avoids process-global ADC / temp files).
    """
    import json

    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    try:
        info = json.loads(service_account_json)
    except ValueError as exc:
        raise ValueError(f"invalid GCS service account JSON: {exc}") from exc
    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/devstorage.read_only"],
    )
    creds.refresh(Request())
    if not creds.token or creds.expiry is None:
        raise ValueError("failed to mint a GCS access token from the service account JSON")
    return pafs.GcsFileSystem(
        access_token=creds.token,
        credential_token_expiration=creds.expiry,
        project_id=project_id or info.get("project_id"),
        default_bucket_location=location or "US",
    )


def _build_filesystem(
    *, kind: str, endpoint: str, access_key_id: str, secret_access_key: str, region: str, path_style: bool
) -> pafs.FileSystem:
    if kind == "azure":
        # access_key_id/secret_access_key double as the storage account
        # name/key here; container addressing (container/blob) mirrors S3's
        # bucket/key, so the read/list code below is shared as-is.
        return pafs.AzureFileSystem(account_name=access_key_id, account_key=secret_access_key)
    if kind == "gcs":
        return _build_gcs_filesystem(
            project_id=access_key_id,
            service_account_json=secret_access_key,
            location=region,
        )
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


def _format_suffix(format: str) -> str:
    """Mirrors `sftp_source_registry._format_suffix` — kept local since the
    two registries don't share a base module."""
    if format == "ndjson":
        return ".ndjson"
    return f".{format}"


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
    suffix = _format_suffix(format)
    keys = sorted(
        info.path[len(bucket) + 1:]
        for info in infos
        if info.type == pafs.FileType.File and info.path.endswith(suffix)
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


async def fetch_for_dataset(
    pool: asyncpg.Pool, tenant_id: str, name: str
) -> tuple[list[dict], Optional[Callable[[], Awaitable[None]]]]:
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

    commit: Optional[Callable[[], Awaitable[None]]] = None
    if new_cursor is not None and new_cursor != row["last_synced_key"]:

        async def _commit_cursor(cursor: str = new_cursor) -> None:
            await pool.execute(
                "UPDATE object_source SET last_synced_key = $1 WHERE tenant_id = $2 AND name = $3",
                cursor, tenant_id, name,
            )

        commit = _commit_cursor

    return rows, commit
