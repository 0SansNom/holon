"""Object-store fetch (S3 / GCS / Azure) for no-code object sources."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional
from urllib.parse import urlsplit

import asyncpg
import pyarrow.csv as pacsv
import pyarrow.fs as pafs
import pyarrow.json as pajson
import pyarrow.parquet as papq
from pyarrow.lib import ArrowException

from app.file_cursor import info_mtime_ns, select_files
from app.source_registry_base import (
    SourceFetchError,
    make_column_cursor_commit,
    resolve_source_secret,
)
from holon_common.connector_safety import (
    ConnectorSafetyError,
    connector_secret,
    pin_object_endpoint,
)

logger = logging.getLogger("connectivity.object_source")

def _build_gcs_filesystem(*, project_id: str, service_account_json: str, location: str) -> pafs.FileSystem:
    """Native GCS via PyArrow, authenticated with a service-account JSON key.

    Mirrors "JSON credentials" / CData AuthScheme=OAuthJWT +
    OAuthJWTCertType=GOOGLEJSON: mint an access token from the key, then
    hand it to GcsFileSystem (avoids process-global ADC / temp files).
    """
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


# open_input_stream() auto-detects these from the extension, so
# `landing/2024.csv.gz` is a readable csv file, not a stray sidecar.
_COMPRESSION_SUFFIXES = ("", ".gz", ".bz2", ".zst", ".lz4")


def _matches_format(path: str, format: str) -> bool:
    lower = path.lower()
    suffix = _format_suffix(format)
    return any(lower.endswith(suffix + compression) for compression in _COMPRESSION_SUFFIXES)


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
    files = [info for info in infos if info.type == pafs.FileType.File]
    matched = [info for info in files if _matches_format(info.path, format)]
    skipped = len(files) - len(matched)
    if skipped:
        # Spark `_SUCCESS` / `.crc` markers are expected; anything else here
        # is a file the user may think is being synced.
        logger.info("object prefix %s/%s: skipped %d file(s) not matching format %r", bucket, key_prefix, skipped, format)
    entries = [(info_mtime_ns(info), info.path[len(bucket) + 1:]) for info in matched]
    if incremental:
        keys, new_cursor = select_files(entries, last_synced_key)
    else:
        keys = sorted(key for _, key in entries)
        new_cursor = None

    rows: list[dict] = []
    for key in keys:
        table = _read_table(fs, f"{bucket}/{key}", format)
        rows.extend(table.to_pylist())

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

    try:
        endpoint = pin_object_endpoint(connection["endpoint"], kind=connection["kind"])
    except ConnectorSafetyError as exc:
        raise SourceFetchError(str(exc)) from exc
    secret_access_key = connector_secret(
        secret_ref=connection["secret_ref"],
        plaintext=connection["secret_access_key"],
        resolved=resolve_source_secret(connection["secret_ref"], tenant_id=tenant_id),
    )

    try:
        rows, new_cursor = await asyncio.to_thread(
            _fetch_sync,
            kind=connection["kind"],
            endpoint=endpoint,
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

        commit = make_column_cursor_commit(
            pool,
            table="object_source",
            tenant_id=tenant_id,
            name=name,
            column="last_synced_key",
            value=new_cursor,
        )

    return rows, commit
