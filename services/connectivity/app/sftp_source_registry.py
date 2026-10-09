"""No-code SFTP source registry — password / secret_ref auth, file or prefix sync."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import stat
import threading
from typing import Awaitable, Callable, Optional

import asyncpg
import paramiko
import pyarrow.csv as pacsv
import pyarrow.json as pajson
import pyarrow.parquet as papq
from pyarrow.lib import ArrowException

from app.file_cursor import mtime_ns_from_stamp, select_files
from app import source_registry_base
from app.source_registry_base import (
    ConnectionInUseError,
    SourceConflictError,
    SourceConfigError,
    SourceFetchError,
    assert_dataset_available,
    make_column_cursor_commit,
    resolve_source_secret,
)

from holon_common.connector_safety import (
    ConnectorSafetyError,
    assert_connector_host,
    pin_connector_host,
    assert_connector_secret_ref,
    assert_destination_change_requires_secret,
    assert_no_inline_connector_secret,
    assert_production_requires_secret_ref,
    connector_secret,
)
from holon_common.security_posture import is_production

logger = logging.getLogger(__name__)

_FORMATS = frozenset({"csv", "ndjson", "parquet"})
# Absolute or relative POSIX-ish paths; no `..`, no nulls, no whitespace tricks.
_REMOTE_PATH_RE = re.compile(r"^/?[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")

_PUBLIC_CONNECTION_COLUMNS = (
    "tenant_id, name, host, port, username, "
    "(password IS NOT NULL OR secret_ref IS NOT NULL) AS has_password, "
    "created_by_urn, created_at"
)

_PUBLIC_SOURCE_COLUMNS = (
    "tenant_id, name, workspace_id, connection_name, remote_path, remote_prefix, format, "
    "incremental, last_synced_path, schedule_interval_minutes, status, created_by_urn, created_at"
)


def _require_remote_path(path: str, *, what: str) -> None:
    if not path or not _REMOTE_PATH_RE.match(path) or ".." in path.split("/"):
        raise SourceConfigError(
            f"invalid {what} {path!r} — use a plain remote path "
            "(e.g. 'upload/data.csv' or 'upload/landing'), no '..'"
        )


async def register_connection(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    host: str,
    username: str,
    created_by_urn: str,
    port: int = 22,
    password: Optional[str] = None,
    secret_ref: Optional[str] = None,
) -> dict:
    """Register or update an SFTP connection credential."""
    if port < 1 or port > 65535:
        raise SourceConfigError("port must be between 1 and 65535")
    existing = await pool.fetchrow(
        "SELECT host, port, username, password, secret_ref FROM sftp_connection WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )
    is_update = existing is not None
    try:
        assert_connector_host(host)
        assert_connector_secret_ref(secret_ref, tenant_id=tenant_id)
        assert_no_inline_connector_secret(password, field="password")
        if existing is not None:
            destination_changed = (
                existing["host"] != host
                or int(existing["port"]) != int(port)
            )
            assert_destination_change_requires_secret(
                is_update=True,
                destination_changed=destination_changed,
                secret_provided=password is not None or secret_ref is not None,
            )
    except ConnectorSafetyError as exc:
        raise SourceConfigError(str(exc)) from exc
    if password is None and secret_ref is None and existing is not None:
        password, secret_ref = existing["password"], existing["secret_ref"]
    try:
        assert_production_requires_secret_ref(secret_ref, is_update=is_update)
    except ConnectorSafetyError as exc:
        raise SourceConfigError(str(exc)) from exc

    await pool.execute(
        """
        INSERT INTO sftp_connection
            (tenant_id, name, host, port, username, password, secret_ref, created_by_urn)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            host = EXCLUDED.host,
            port = EXCLUDED.port,
            username = EXCLUDED.username,
            password = EXCLUDED.password,
            secret_ref = EXCLUDED.secret_ref
        """,
        tenant_id, name, host, port, username, password, secret_ref, created_by_urn,
    )
    return await get_connection(pool, tenant_id, name)


async def get_connection(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        f"SELECT {_PUBLIC_CONNECTION_COLUMNS} FROM sftp_connection WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )
    return None if row is None else dict(row)


async def list_connections(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        f"SELECT {_PUBLIC_CONNECTION_COLUMNS} FROM sftp_connection WHERE tenant_id = $1 ORDER BY name",
        tenant_id,
    )
    return [dict(row) for row in rows]


async def delete_connection(pool: asyncpg.Pool, tenant_id: str, name: str) -> None:
    await source_registry_base.delete_connection_if_unused(
        pool,
        connection_table="sftp_connection",
        source_table="sftp_source",
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
    format: str,
    created_by_urn: str,
    remote_path: Optional[str] = None,
    remote_prefix: Optional[str] = None,
    incremental: bool = False,
    schedule_interval_minutes: Optional[int] = None,
    reserved_dataset_names: frozenset[str] = frozenset(),
) -> dict:
    """Verify dataset name availability and validate SFTP source parameters."""
    if bool(remote_path) == bool(remote_prefix):
        raise SourceConfigError("exactly one of remote_path or remote_prefix must be set")
    if format not in _FORMATS:
        raise SourceConfigError(f"format must be one of {sorted(_FORMATS)}")
    if remote_path:
        _require_remote_path(remote_path, what="remote_path")
    if remote_prefix:
        _require_remote_path(remote_prefix, what="remote_prefix")
    if incremental and remote_path:
        raise SourceConfigError("incremental only applies to remote_prefix sources, not a single remote_path")
    if await get_connection(pool, tenant_id, connection_name) is None:
        raise SourceConfigError(f"unknown connection: {connection_name!r}")
    if schedule_interval_minutes is not None and schedule_interval_minutes <= 0:
        raise SourceConfigError("schedule_interval_minutes must be a positive number of minutes")

    await assert_dataset_available(
        pool,
        tenant_id=tenant_id,
        name=name,
        reserved_dataset_names=reserved_dataset_names,
        exclude_table="sftp_source",
    )

    await pool.execute(
        """
        INSERT INTO sftp_source
            (tenant_id, name, workspace_id, connection_name, remote_path, remote_prefix, format,
             incremental, schedule_interval_minutes, status, created_by_urn)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'active', $10)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            workspace_id = EXCLUDED.workspace_id,
            connection_name = EXCLUDED.connection_name,
            remote_path = EXCLUDED.remote_path,
            remote_prefix = EXCLUDED.remote_prefix,
            format = EXCLUDED.format,
            incremental = EXCLUDED.incremental,
            schedule_interval_minutes = EXCLUDED.schedule_interval_minutes,
            status = 'active'
        """,
        tenant_id, name, workspace_id, connection_name, remote_path, remote_prefix, format,
        incremental, schedule_interval_minutes, created_by_urn,
    )
    return await get_source(pool, tenant_id, name)


async def list_scheduled_sources(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    return await source_registry_base.list_scheduled_sources_rows(pool, table="sftp_source", tenant_id=tenant_id)

async def list_all_scheduled_sources(pool: asyncpg.Pool) -> list[dict]:
    return await source_registry_base.list_all_scheduled_sources_rows(pool, table="sftp_source")

async def set_source_status(pool: asyncpg.Pool, tenant_id: str, name: str, status: str) -> Optional[dict]:
    return await source_registry_base.set_source_status_row(
        pool, table="sftp_source", public_columns=_PUBLIC_SOURCE_COLUMNS, tenant_id=tenant_id, name=name, status=status
    )

async def delete_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> None:
    await source_registry_base.delete_source_row(pool, table="sftp_source", tenant_id=tenant_id, name=name)

async def get_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    return await source_registry_base.get_source_row(
        pool, table="sftp_source", public_columns=_PUBLIC_SOURCE_COLUMNS, tenant_id=tenant_id, name=name
    )

async def list_sources(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    return await source_registry_base.list_sources_for_tenant(
        pool, table="sftp_source", public_columns=_PUBLIC_SOURCE_COLUMNS, tenant_id=tenant_id
    )

async def is_registered(pool: asyncpg.Pool, tenant_id: str, name: str) -> bool:
    return await source_registry_base.is_source_active(pool, table="sftp_source", tenant_id=tenant_id, name=name)

from app.sftp_source_fetch import (  # noqa: E402,F401
    _configure_host_key_policy,
    _fetch_sync,
    _format_suffix,
    _read_bytes,
    _truthy,
    _warn_insecure_auto_add_once,
    fetch_for_dataset,
)
