"""No-code SQL source registry for Postgres-wire databases."""

from __future__ import annotations

import re
from typing import Any, Optional

import asyncpg

from holon_common.connector_safety import (
    ConnectorSafetyError,
    assert_connector_host,
    assert_connector_secret_ref,
    assert_no_inline_connector_secret,
    assert_production_requires_secret_ref,
)
from holon_common.secrets import resolve_optional
from holon_common.sql_ident import quote_identifier, require_identifier

DDL = """
CREATE TABLE IF NOT EXISTS sql_connection (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 5432,
    database TEXT NOT NULL,
    username TEXT NOT NULL,
    password TEXT,
    secret_ref TEXT,
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS sql_source (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    connection_name TEXT NOT NULL,
    table_name TEXT,
    query TEXT,
    schedule_interval_minutes INTEGER,
    cursor_property TEXT,
    last_cursor_value TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);
"""

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

_PUBLIC_CONNECTION_COLUMNS = (
    "tenant_id, name, host, port, database, username, (password IS NOT NULL OR secret_ref IS NOT NULL) AS has_password, "
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


class ConnectionConflictError(ValueError):
    pass


class ConnectionInUseError(ValueError):
    pass


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


_quote_identifier = quote_identifier


def _require_select_only(query: str) -> None:
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




async def register_connection(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    host: str,
    port: int,
    database: str,
    username: str,
    created_by_urn: str,
    password: Optional[str] = None,
    secret_ref: Optional[str] = None,
) -> dict:
    """Register or update a SQL connection credential."""
    existing = await pool.fetchrow(
        "SELECT password, secret_ref FROM sql_connection WHERE tenant_id = $1 AND name = $2",
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
        INSERT INTO sql_connection (tenant_id, name, host, port, database, username, password, secret_ref, created_by_urn)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            host = EXCLUDED.host,
            port = EXCLUDED.port,
            database = EXCLUDED.database,
            username = EXCLUDED.username,
            password = EXCLUDED.password,
            secret_ref = EXCLUDED.secret_ref
        """,
        tenant_id, name, host, port, database, username, password, secret_ref, created_by_urn,
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
    if query:
        _require_select_only(query)
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

    conflicting_object_source = await pool.fetchval(
        "SELECT name FROM object_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    )
    if conflicting_object_source is not None:
        raise SourceConflictError(f"dataset {name!r} is already claimed by active object source {conflicting_object_source!r}")

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


async def fetch_for_dataset(pool: asyncpg.Pool, tenant_id: str, name: str) -> list[dict]:
    row = await pool.fetchrow(
        "SELECT connection_name, table_name, query, cursor_property, last_cursor_value "
        "FROM sql_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    )
    if row is None:
        raise SourceFetchError(f"no active SQL source registered as {name!r}")

    connection = await pool.fetchrow(
        "SELECT host, port, database, username, password, secret_ref FROM sql_connection WHERE tenant_id = $1 AND name = $2",
        tenant_id, row["connection_name"],
    )
    if connection is None:
        raise SourceFetchError(f"source {name!r} references connection {row['connection_name']!r}, which no longer exists")
    try:
        assert_connector_host(connection["host"])
    except ConnectorSafetyError as exc:
        raise SourceFetchError(str(exc)) from exc
    password = resolve_optional(connection["secret_ref"]) or connection["password"]

    try:
        conn = await asyncpg.connect(
            host=connection["host"], port=connection["port"], database=connection["database"],
            user=connection["username"], password=password, timeout=15.0,
        )
    except (OSError, asyncpg.PostgresError) as exc:
        raise SourceFetchError(f"could not connect to source {name!r}: {exc}") from exc
    try:
        try:
            if row["table_name"]:
                sql = f"SELECT * FROM {_quote_identifier(row['table_name'])}"
                args: list[Any] = []
                if row["cursor_property"] and row["last_cursor_value"] is not None:
                    # Cast last_cursor_value text to column data type for accurate comparison
                    column_type = await conn.fetchval(
                        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                        "WHERE attrelid = $1::regclass AND attname = $2 AND NOT attisdropped",
                        row["table_name"], row["cursor_property"],
                    )
                    if column_type is None:
                        raise SourceFetchError(
                            f"source {name!r}: cursor_property {row['cursor_property']!r} "
                            f"not found on table {row['table_name']!r}"
                        )
                    sql += f" WHERE {quote_identifier(row['cursor_property'])} > $1::text::{column_type}"
                    args.append(row["last_cursor_value"])
            else:
                sql = row["query"]
                args = []
            records = await conn.fetch(sql, *args)
        except asyncpg.PostgresError as exc:
            raise SourceFetchError(f"query failed for source {name!r}: {exc}") from exc
    finally:
        await conn.close()

    rows = [dict(record) for record in records]

    if row["cursor_property"]:
        candidates = [r[row["cursor_property"]] for r in rows if r.get(row["cursor_property"]) is not None]
        if candidates:
            new_cursor = str(max(candidates))
            if new_cursor != row["last_cursor_value"]:
                await pool.execute(
                    "UPDATE sql_source SET last_cursor_value = $1 WHERE tenant_id = $2 AND name = $3",
                    new_cursor, tenant_id, name,
                )

    return rows
