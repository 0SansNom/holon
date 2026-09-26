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

from holon_common.connector_safety import (
    ConnectorSafetyError,
    assert_connector_host,
    assert_connector_secret_ref,
    assert_no_inline_connector_secret,
    assert_production_requires_secret_ref,
    resolve_connector_secret,
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


class SourceConflictError(ValueError):
    pass


class SourceConfigError(ValueError):
    pass


class SourceFetchError(ValueError):
    pass


def _resolve_secret(ref, tenant_id: str):
    """Resolve a stored secret_ref, re-checking tenant scope at fetch time."""
    try:
        return resolve_connector_secret(ref, tenant_id=tenant_id)
    except ConnectorSafetyError as exc:
        raise SourceFetchError(str(exc)) from exc


class ConnectionInUseError(ValueError):
    pass


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
        "SELECT password, secret_ref FROM sftp_connection WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )
    is_update = existing is not None
    try:
        assert_connector_host(host)
        assert_connector_secret_ref(secret_ref, tenant_id=tenant_id)
        assert_no_inline_connector_secret(password, field="password")
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
    in_use = await pool.fetch(
        "SELECT name FROM sftp_source WHERE tenant_id = $1 AND connection_name = $2", tenant_id, name
    )
    if in_use:
        source_names = [row["name"] for row in in_use]
        raise ConnectionInUseError(
            f"connection {name!r} is still used by source(s) {source_names} — repoint or delete them first"
        )
    await pool.execute("DELETE FROM sftp_connection WHERE tenant_id = $1 AND name = $2", tenant_id, name)


async def _assert_dataset_available(
    pool: asyncpg.Pool, *, tenant_id: str, name: str, reserved_dataset_names: frozenset[str]
) -> None:
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

    # Do not conflict-check sftp_source itself — re-register updates via ON CONFLICT.
    for table, label in (
        ("generic_rest_source", "REST source"),
        ("sql_source", "SQL source"),
        ("object_source", "object source"),
        ("salesforce_source", "Salesforce source"),
    ):
        conflicting = await pool.fetchval(
            f"SELECT name FROM {table} WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
            tenant_id, name,
        )
        if conflicting is not None:
            raise SourceConflictError(f"dataset {name!r} is already claimed by active {label} {conflicting!r}")


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

    await _assert_dataset_available(
        pool, tenant_id=tenant_id, name=name, reserved_dataset_names=reserved_dataset_names
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
    rows = await pool.fetch(
        "SELECT name, schedule_interval_minutes FROM sftp_source "
        "WHERE tenant_id = $1 AND status = 'active' AND schedule_interval_minutes IS NOT NULL",
        tenant_id,
    )
    return [dict(row) for row in rows]


async def list_all_scheduled_sources(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch(
        "SELECT tenant_id, name, workspace_id, schedule_interval_minutes FROM sftp_source "
        "WHERE status = 'active' AND schedule_interval_minutes IS NOT NULL"
    )
    return [dict(row) for row in rows]


async def set_source_status(pool: asyncpg.Pool, tenant_id: str, name: str, status: str) -> Optional[dict]:
    await pool.execute(
        "UPDATE sftp_source SET status = $1 WHERE tenant_id = $2 AND name = $3", status, tenant_id, name
    )
    return await get_source(pool, tenant_id, name)


async def delete_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> None:
    await pool.execute("DELETE FROM sftp_source WHERE tenant_id = $1 AND name = $2", tenant_id, name)


async def get_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        f"SELECT {_PUBLIC_SOURCE_COLUMNS} FROM sftp_source WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )
    return None if row is None else dict(row)


async def list_sources(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        f"SELECT {_PUBLIC_SOURCE_COLUMNS} FROM sftp_source WHERE tenant_id = $1 ORDER BY name", tenant_id
    )
    return [dict(row) for row in rows]


async def is_registered(pool: asyncpg.Pool, tenant_id: str, name: str) -> bool:
    return await pool.fetchval(
        "SELECT true FROM sftp_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    ) or False


def _read_bytes(data: bytes, format: str) -> list[dict]:
    buf = io.BytesIO(data)
    if format == "csv":
        return pacsv.read_csv(buf).to_pylist()
    if format == "ndjson":
        return pajson.read_json(buf).to_pylist()
    return papq.read_table(buf).to_pylist()


def _format_suffix(format: str) -> str:
    if format == "ndjson":
        return ".ndjson"
    return f".{format}"


def _truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes"}


_insecure_auto_add_warned = threading.Lock()
_insecure_auto_add_warned_flag = False


def _warn_insecure_auto_add_once() -> None:
    global _insecure_auto_add_warned_flag
    with _insecure_auto_add_warned:
        if _insecure_auto_add_warned_flag:
            return
        _insecure_auto_add_warned_flag = True
    logger.warning(
        "HOLON_SFTP_INSECURE_AUTO_ADD_HOSTKEY is set — trusting unknown SFTP host keys "
        "on first connect (AutoAddPolicy). This is only safe for local/demo/CI use; "
        "set HOLON_SFTP_KNOWN_HOSTS to a pinned known_hosts file for real deployments."
    )


def _configure_host_key_policy(client: paramiko.SSHClient) -> None:
    """Set the SFTP client's host-key verification policy.

    Resolution order:
      1. `HOLON_SFTP_KNOWN_HOSTS` set: load system host keys plus that file
         and reject anything not already pinned there (`RejectPolicy`).
      2. Production (`HOLON_ENV=production`): always fail closed
         (`RejectPolicy`) — host key trust must be pinned via
         `HOLON_SFTP_KNOWN_HOSTS`, never auto-accepted, even if the
         insecure opt-in below is (mis)configured.
      3. `HOLON_SFTP_INSECURE_AUTO_ADD_HOSTKEY` truthy: explicit opt-in to
         `AutoAddPolicy`, for local/demo/CI SFTP fixtures with no pinned
         host key. Warned once per process.
      4. Otherwise: fail closed (`RejectPolicy`) — no known_hosts and no
         explicit insecure opt-in means we refuse to trust an unknown host
         key rather than silently auto-accepting it.
    """
    known_hosts_path = (os.environ.get("HOLON_SFTP_KNOWN_HOSTS") or "").strip()
    if known_hosts_path:
        client.load_system_host_keys()
        client.load_host_keys(known_hosts_path)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        return

    if is_production():
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        return

    if _truthy("HOLON_SFTP_INSECURE_AUTO_ADD_HOSTKEY"):
        _warn_insecure_auto_add_once()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        return

    client.set_missing_host_key_policy(paramiko.RejectPolicy())


def _fetch_sync(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    remote_path: Optional[str],
    remote_prefix: Optional[str],
    format: str,
    incremental: bool,
    last_synced_path: Optional[str],
) -> tuple[list[dict], Optional[str]]:
    client = paramiko.SSHClient()
    _configure_host_key_policy(client)
    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            look_for_keys=False,
            allow_agent=False,
            timeout=15,
        )
        sftp = client.open_sftp()
        try:
            if remote_path:
                with sftp.open(remote_path, "rb") as handle:
                    return _read_bytes(handle.read(), format), None

            prefix = remote_prefix or ""
            # List one directory level when prefix has no trailing slash file match;
            # walk recursively under the prefix directory.
            listed: list[tuple[int, str]] = []

            def _walk(directory: str) -> None:
                try:
                    entries = sftp.listdir_attr(directory)
                except OSError:
                    return
                for entry in entries:
                    child = f"{directory.rstrip('/')}/{entry.filename}"
                    mode = entry.st_mode or 0
                    if stat.S_ISDIR(mode):
                        _walk(child)
                    elif stat.S_ISREG(mode) and child.endswith(_format_suffix(format)):
                        listed.append((mtime_ns_from_stamp(entry.st_mtime), child))

            # If prefix points at a directory, walk it; if it is a path prefix
            # of filenames in a parent dir, list that parent and filter.
            try:
                attr = sftp.stat(prefix)
            except OSError:
                attr = None
            if attr is not None and stat.S_ISDIR(attr.st_mode or 0):
                _walk(prefix)
            else:
                parent = prefix.rsplit("/", 1)[0] if "/" in prefix else "."
                base = prefix if "/" not in prefix else prefix.rsplit("/", 1)[-1]
                try:
                    for entry in sftp.listdir_attr(parent):
                        child = f"{parent.rstrip('/')}/{entry.filename}" if parent != "." else entry.filename
                        mode = entry.st_mode or 0
                        if stat.S_ISREG(mode) and entry.filename.startswith(base) and child.endswith(
                            _format_suffix(format)
                        ):
                            listed.append((mtime_ns_from_stamp(entry.st_mtime), child))
                        elif stat.S_ISDIR(mode) and entry.filename.startswith(base):
                            _walk(child)
                except OSError as exc:
                    raise SourceFetchError(f"could not list remote prefix {prefix!r}: {exc}") from exc

            if incremental:
                paths, new_cursor = select_files(listed, last_synced_path)
            else:
                paths = sorted(key for _, key in listed)
                new_cursor = None

            rows: list[dict] = []
            for path in paths:
                with sftp.open(path, "rb") as handle:
                    rows.extend(_read_bytes(handle.read(), format))
            return rows, (new_cursor if incremental else None)
        finally:
            sftp.close()
    finally:
        client.close()


async def fetch_for_dataset(
    pool: asyncpg.Pool, tenant_id: str, name: str
) -> tuple[list[dict], Optional[Callable[[], Awaitable[None]]]]:
    row = await pool.fetchrow(
        "SELECT connection_name, remote_path, remote_prefix, format, incremental, last_synced_path "
        "FROM sftp_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    )
    if row is None:
        raise SourceFetchError(f"no active SFTP source registered as {name!r}")

    connection = await pool.fetchrow(
        "SELECT host, port, username, password, secret_ref FROM sftp_connection "
        "WHERE tenant_id = $1 AND name = $2",
        tenant_id, row["connection_name"],
    )
    if connection is None:
        raise SourceFetchError(
            f"source {name!r} references connection {row['connection_name']!r}, which no longer exists"
        )
    try:
        assert_connector_host(connection["host"])
    except ConnectorSafetyError as exc:
        raise SourceFetchError(str(exc)) from exc
    password = _resolve_secret(connection["secret_ref"], tenant_id) or connection["password"] or ""

    try:
        rows, new_cursor = await asyncio.to_thread(
            _fetch_sync,
            host=connection["host"],
            port=connection["port"],
            username=connection["username"],
            password=password,
            remote_path=row["remote_path"],
            remote_prefix=row["remote_prefix"],
            format=row["format"],
            incremental=row["incremental"],
            last_synced_path=row["last_synced_path"],
        )
    except SourceFetchError:
        raise
    except (OSError, ValueError, ArrowException, paramiko.SSHException) as exc:
        raise SourceFetchError(f"could not read source {name!r}: {exc}") from exc

    commit: Optional[Callable[[], Awaitable[None]]] = None
    if new_cursor is not None and new_cursor != row["last_synced_path"]:

        async def _commit_cursor(cursor: str = new_cursor) -> None:
            await pool.execute(
                "UPDATE sftp_source SET last_synced_path = $1 WHERE tenant_id = $2 AND name = $3",
                cursor, tenant_id, name,
            )

        commit = _commit_cursor

    return rows, commit
