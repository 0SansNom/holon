"""No-code SQL source registry for Postgres, MySQL/MariaDB, and SQL Server."""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable, Optional

import asyncpg

from holon_common.connector_safety import (
    ConnectorSafetyError,
    assert_connector_host,
    assert_connector_secret_ref,
    assert_destination_change_requires_secret,
    assert_no_inline_connector_secret,
    assert_production_requires_secret_ref,
    resolve_connector_secret,
)
from holon_common.sql_ident import quote_identifier, require_identifier

from app import sql_drivers

_FORBIDDEN_STMT = re.compile(
    r"\b(insert|update|delete|truncate|alter|drop|create|grant|revoke|call|execute)\b",
    re.IGNORECASE,
)
_COPY_STMT = re.compile(r"(^\s*copy\b|\bcopy\s+\S+\s+(from|to)\b)", re.IGNORECASE)
_FORBIDDEN_FUNCS = re.compile(
    r"\b(pg_read_\w+|pg_ls_\w+|pg_file_\w+|pg_write_\w+|lo_import|lo_export|lo_get|lo_put|"
    r"lo_from_bytea|lo_create|lo_unlink|dblink\w*|pg_sleep)\s*\(",
    re.IGNORECASE,
)
_SELECT_INTO = re.compile(
    r"\binto\s+(temp(orary)?\s+)?(table\s+)?[\"']?[A-Za-z_]",
    re.IGNORECASE,
)
_FOR_LOCK = re.compile(r"\bfor\s+(update|share|no\s+key\s+update|key\s+share)\b", re.IGNORECASE)
# MySQL / MariaDB exfiltration and side-effect helpers.
_MYSQL_FORBIDDEN = re.compile(
    r"\b(load_file\s*\(|into\s+outfile\b|into\s+dumpfile\b|benchmark\s*\(|sleep\s*\()",
    re.IGNORECASE,
)
# SQL Server file / OLE / extended-proc / linked-server helpers.
_MSSQL_FORBIDDEN = re.compile(
    r"\b(openrowset\s*\(|opendatasource\s*\(|openquery\s*\(|xp_\w+|sp_oacreate\b)",
    re.IGNORECASE,
)

_PUBLIC_CONNECTION_COLUMNS = (
    "tenant_id, name, dialect, host, port, database, username, "
    "(password IS NOT NULL OR secret_ref IS NOT NULL) AS has_password, "
    "created_by_urn, created_at"
)

_PUBLIC_SOURCE_COLUMNS = (
    "tenant_id, name, workspace_id, connection_name, table_name, query, schedule_interval_minutes, "
    "cursor_property, last_cursor_value, status, created_by_urn, created_at"
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


class ConnectionConflictError(ValueError):
    pass


class ConnectionInUseError(ValueError):
    pass


_quote_identifier = quote_identifier


def _require_select_only(query: str, dialect: str = "postgres") -> None:
    stripped = query.strip().rstrip(";").strip()
    if ";" in stripped:
        raise SourceConfigError("query must be a single SELECT statement — no semicolons")
    head = stripped.split(None, 1)[0].upper() if stripped else ""
    if head not in {"SELECT", "WITH"}:
        raise SourceConfigError("query must start with SELECT or WITH — this connector is read-only")
    if (
        _FORBIDDEN_STMT.search(stripped)
        or _COPY_STMT.search(stripped)
        or _FORBIDDEN_FUNCS.search(stripped)
        or _FOR_LOCK.search(stripped)
        or _SELECT_INTO.search(stripped)
    ):
        raise SourceConfigError("query must be a read-only SELECT — writes, locks, and file helpers are not allowed")
    try:
        d = sql_drivers.normalize_dialect(dialect)
    except ValueError as exc:
        raise SourceConfigError(str(exc)) from exc
    if d == "mysql" and _MYSQL_FORBIDDEN.search(stripped):
        raise SourceConfigError("query must be a read-only SELECT — MySQL file helpers are not allowed")
    if d == "mssql" and _MSSQL_FORBIDDEN.search(stripped):
        raise SourceConfigError("query must be a read-only SELECT — SQL Server file helpers are not allowed")


def _bind_cursor_value(value: str) -> Any:
    """Coerce a stored cursor string so drivers can bind typed columns.

    Avoids a dialect-specific catalog lookup (pg_attribute) while still
    comparing integers/floats natively instead of lexicographically.
    """
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        return int(value)
    try:
        return float(value)
    except ValueError:
        return value


async def register_connection(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    host: str,
    database: str,
    username: str,
    created_by_urn: str,
    dialect: str = "postgres",
    port: Optional[int] = None,
    password: Optional[str] = None,
    secret_ref: Optional[str] = None,
) -> dict:
    """Register or update a SQL connection credential."""
    try:
        dialect = sql_drivers.normalize_dialect(dialect)
    except ValueError as exc:
        raise SourceConfigError(str(exc)) from exc
    if port is None:
        port = sql_drivers.default_port_for(dialect)

    existing = await pool.fetchrow(
        "SELECT host, port, dialect, database, username, password, secret_ref "
        "FROM sql_connection WHERE tenant_id = $1 AND name = $2",
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
                or (existing["dialect"] or "postgres") != dialect
                or existing["database"] != database
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
        INSERT INTO sql_connection
            (tenant_id, name, dialect, host, port, database, username, password, secret_ref, created_by_urn)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            dialect = EXCLUDED.dialect,
            host = EXCLUDED.host,
            port = EXCLUDED.port,
            database = EXCLUDED.database,
            username = EXCLUDED.username,
            password = EXCLUDED.password,
            secret_ref = EXCLUDED.secret_ref
        """,
        tenant_id, name, dialect, host, port, database, username, password, secret_ref, created_by_urn,
    )
    return await get_connection(pool, tenant_id, name)


async def get_connection(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        f"SELECT {_PUBLIC_CONNECTION_COLUMNS} FROM sql_connection WHERE tenant_id = $1 AND name = $2", tenant_id, name
    )
    return None if row is None else dict(row)


async def list_connections(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        f"SELECT {_PUBLIC_CONNECTION_COLUMNS} FROM sql_connection WHERE tenant_id = $1 ORDER BY name", tenant_id
    )
    return [dict(row) for row in rows]


async def delete_connection(pool: asyncpg.Pool, tenant_id: str, name: str) -> None:
    in_use = await pool.fetch(
        "SELECT name FROM sql_source WHERE tenant_id = $1 AND connection_name = $2", tenant_id, name
    )
    if in_use:
        source_names = [row["name"] for row in in_use]
        raise ConnectionInUseError(
            f"connection {name!r} is still used by source(s) {source_names} — repoint or delete them first"
        )
    await pool.execute("DELETE FROM sql_connection WHERE tenant_id = $1 AND name = $2", tenant_id, name)


async def register_source(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    workspace_id: str,
    connection_name: str,
    created_by_urn: str,
    table_name: Optional[str] = None,
    query: Optional[str] = None,
    schedule_interval_minutes: Optional[int] = None,
    cursor_property: Optional[str] = None,
    reserved_dataset_names: frozenset[str] = frozenset(),
) -> dict:
    """Verify dataset name availability and validate SQL source parameters."""
    if bool(table_name) == bool(query):
        raise SourceConfigError("exactly one of table_name or query must be set")
    if table_name:
        try:
            require_identifier(table_name, what="table_name")
        except ValueError as exc:
            raise SourceConfigError(str(exc)) from exc
    if cursor_property:
        try:
            require_identifier(cursor_property, what="cursor_property")
        except ValueError as exc:
            raise SourceConfigError(str(exc)) from exc

    connection = await get_connection(pool, tenant_id, connection_name)
    if connection is None:
        raise SourceConfigError(f"unknown connection: {connection_name!r}")
    dialect = connection.get("dialect") or "postgres"
    if query:
        _require_select_only(query, dialect)
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

    conflicting_object_source = await pool.fetchval(
        "SELECT name FROM object_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    )
    if conflicting_object_source is not None:
        raise SourceConflictError(f"dataset {name!r} is already claimed by active object source {conflicting_object_source!r}")

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
        INSERT INTO sql_source
            (tenant_id, name, workspace_id, connection_name, table_name, query,
             schedule_interval_minutes, cursor_property, status, created_by_urn)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'active', $9)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            workspace_id = EXCLUDED.workspace_id,
            connection_name = EXCLUDED.connection_name,
            table_name = EXCLUDED.table_name,
            query = EXCLUDED.query,
            schedule_interval_minutes = EXCLUDED.schedule_interval_minutes,
            cursor_property = EXCLUDED.cursor_property,
            -- last_cursor_value deliberately absent: resume state
            -- computed by fetch_for_dataset, not a form field.
            status = 'active'
        """,
        tenant_id, name, workspace_id, connection_name, table_name, query,
        schedule_interval_minutes, cursor_property, created_by_urn,
    )
    return await get_source(pool, tenant_id, name)


async def list_scheduled_sources(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT name, schedule_interval_minutes FROM sql_source "
        "WHERE tenant_id = $1 AND status = 'active' AND schedule_interval_minutes IS NOT NULL",
        tenant_id,
    )
    return [dict(row) for row in rows]


async def list_all_scheduled_sources(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch(
        "SELECT tenant_id, name, workspace_id, schedule_interval_minutes FROM sql_source "
        "WHERE status = 'active' AND schedule_interval_minutes IS NOT NULL"
    )
    return [dict(row) for row in rows]


async def set_source_status(pool: asyncpg.Pool, tenant_id: str, name: str, status: str) -> Optional[dict]:
    await pool.execute(
        "UPDATE sql_source SET status = $1 WHERE tenant_id = $2 AND name = $3", status, tenant_id, name
    )
    return await get_source(pool, tenant_id, name)


async def delete_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> None:
    await pool.execute("DELETE FROM sql_source WHERE tenant_id = $1 AND name = $2", tenant_id, name)


async def get_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        f"SELECT {_PUBLIC_SOURCE_COLUMNS} FROM sql_source WHERE tenant_id = $1 AND name = $2", tenant_id, name
    )
    return None if row is None else dict(row)


async def list_sources(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        f"SELECT {_PUBLIC_SOURCE_COLUMNS} FROM sql_source WHERE tenant_id = $1 ORDER BY name", tenant_id
    )
    return [dict(row) for row in rows]


async def is_registered(pool: asyncpg.Pool, tenant_id: str, name: str) -> bool:
    return await pool.fetchval(
        "SELECT true FROM sql_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    ) or False


async def fetch_for_dataset(
    pool: asyncpg.Pool, tenant_id: str, name: str
) -> tuple[list[dict], Optional[Callable[[], Awaitable[None]]]]:
    row = await pool.fetchrow(
        "SELECT connection_name, table_name, query, cursor_property, last_cursor_value "
        "FROM sql_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    )
    if row is None:
        raise SourceFetchError(f"no active SQL source registered as {name!r}")

    connection = await pool.fetchrow(
        "SELECT dialect, host, port, database, username, password, secret_ref "
        "FROM sql_connection WHERE tenant_id = $1 AND name = $2",
        tenant_id, row["connection_name"],
    )
    if connection is None:
        raise SourceFetchError(f"source {name!r} references connection {row['connection_name']!r}, which no longer exists")
    try:
        assert_connector_host(connection["host"])
    except ConnectorSafetyError as exc:
        raise SourceFetchError(str(exc)) from exc
    password = _resolve_secret(connection["secret_ref"], tenant_id) or connection["password"]

    try:
        dialect = sql_drivers.normalize_dialect(connection["dialect"])
    except ValueError as exc:
        raise SourceFetchError(str(exc)) from exc

    if row["table_name"]:
        sql = f"SELECT * FROM {quote_identifier(row['table_name'], dialect=dialect)}"
        args: list[Any] = []
        if row["cursor_property"] and row["last_cursor_value"] is not None:
            # Uniform bind across dialects (no pg_attribute type cast).
            col = quote_identifier(row["cursor_property"], dialect=dialect)
            sql += f" WHERE {col} > {sql_drivers.cursor_placeholder(dialect)}"
            args.append(_bind_cursor_value(row["last_cursor_value"]))
    else:
        sql = row["query"]
        args = []
        try:
            _require_select_only(sql, dialect)
        except SourceConfigError as exc:
            raise SourceFetchError(str(exc)) from exc

    try:
        rows = await sql_drivers.fetch_dicts(
            dialect=dialect,
            host=connection["host"],
            port=connection["port"],
            database=connection["database"],
            username=connection["username"],
            password=password,
            sql=sql,
            args=args,
        )
    except Exception as exc:
        # Drivers raise a mix of OSError, asyncpg/aiomysql/aioodbc errors.
        raise SourceFetchError(f"could not fetch source {name!r}: {exc}") from exc

    commit: Optional[Callable[[], Awaitable[None]]] = None
    if row["cursor_property"]:
        candidates = [r[row["cursor_property"]] for r in rows if r.get(row["cursor_property"]) is not None]
        if candidates:
            new_cursor = str(max(candidates))
            if new_cursor != row["last_cursor_value"]:

                async def _commit_cursor(cursor: str = new_cursor) -> None:
                    await pool.execute(
                        "UPDATE sql_source SET last_cursor_value = $1 WHERE tenant_id = $2 AND name = $3",
                        cursor, tenant_id, name,
                    )

                commit = _commit_cursor

    return rows, commit
