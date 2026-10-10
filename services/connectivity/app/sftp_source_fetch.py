"""SFTP fetch / host-key policy for no-code SFTP sources."""
from __future__ import annotations

import asyncio
import io
import logging
import os
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
from app.source_registry_base import (
    SourceFetchError,
    make_column_cursor_commit,
    resolve_source_secret,
)
from holon_common.connector_safety import (
    ConnectorSafetyError,
    connector_secret,
    pin_connector_host,
)
from holon_common.security_posture import is_production

logger = logging.getLogger(__name__)


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
        pinned_host = pin_connector_host(connection["host"])
    except ConnectorSafetyError as exc:
        raise SourceFetchError(str(exc)) from exc
    password = connector_secret(
        secret_ref=connection["secret_ref"],
        plaintext=connection["password"],
        resolved=resolve_source_secret(connection["secret_ref"], tenant_id=tenant_id),
    ) or ""

    try:
        rows, new_cursor = await asyncio.to_thread(
            _fetch_sync,
            host=pinned_host,
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

        commit = make_column_cursor_commit(
            pool,
            table="sftp_source",
            tenant_id=tenant_id,
            name=name,
            column="last_synced_path",
            value=new_cursor,
        )

    return rows, commit

