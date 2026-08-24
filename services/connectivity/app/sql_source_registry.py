"""No-code SQL source registry — Postgres-wire connector registry.

Targets the Postgres wire protocol specifically: PostgreSQL, Amazon
Redshift, and CockroachDB all speak it, so one connector (via `asyncpg`,
already a Connectivity dependency for its own DB — no new dependency)
covers three real products.

Mirrors `generic_source_registry.py`'s shape for the same reason that
registry has it: `run_sync` dispatches over one shared `dataset`
namespace every registry is an equal claimant on. A connection is
mandatory for every source here (unlike the REST connector's optional
inline auth) — a SQL source almost always shares its database with
several sibling tables, so requiring the credential to be registered
once, up front, is the natural default rather than an added mode.
"""

from __future__ import annotations

import re
from typing import Any, Optional

import asyncpg

from holon_common.secrets import resolve_optional

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

# Never bindable as a query parameter (identifiers aren't values), so a
# table_name is validated against this instead of being trusted as-is.
# Optionally schema-qualified (`public.orders`).
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")

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


def _quote_identifier(table_name: str) -> str:
    """Wraps each dot-separated part in double quotes — safe only because
    the caller has already validated `table_name` against `_IDENTIFIER_RE`,
    which rejects anything containing a quote, whitespace, or semicolon."""
    return ".".join(f'"{part}"' for part in table_name.split("."))


def _require_select_only(query: str) -> None:
    stripped = query.strip().rstrip(";").strip()
    if ";" in stripped:
        raise SourceConfigError("query must be a single SELECT statement — no semicolons")
    if not stripped[:6].upper() == "SELECT":
        raise SourceConfigError("query must start with SELECT — this connector is read-only")


# ---- connections --------------------------------------------------------


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
    """A real upsert (same `name` again updates it). Omitted `password`
    keeps the existing one — same "resolve in Python before it reaches
    SQL" treatment `generic_source_registry.register_connection` already
    gives its own secrets, for the same reason (an edit that doesn't
    resend the secret must not blank it out).
    """
    if password is None and secret_ref is None:
        existing = await pool.fetchrow(
            "SELECT password, secret_ref FROM sql_connection WHERE tenant_id = $1 AND name = $2",
            tenant_id, name,
        )
        if existing is not None:
            password, secret_ref = existing["password"], existing["secret_ref"]

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


# ---- sources --------------------------------------------------------------


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
    """Same dataset-ownership guard `generic_source_registry.register_source`
    already enforces — this registry is an equal claimant on the same
    `dataset` namespace `run_sync` dispatches over.

    Exactly one of `table_name` / `query` — same "which one would
    `fetch_for_dataset` even trust" reasoning as that registry's
    `connection_name` vs. inline-auth mutual exclusion.
    """
    if bool(table_name) == bool(query):
        raise SourceConfigError("exactly one of table_name or query must be set")
    if table_name and not _IDENTIFIER_RE.match(table_name):
        raise SourceConfigError(
            f"invalid table_name {table_name!r} — must be a plain identifier, optionally schema-qualified "
            "(e.g. 'orders' or 'public.orders'), no quotes/whitespace/punctuation"
        )
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
                    # last_cursor_value is stored as TEXT (it started life
                    # as whatever type the cursor column is), so a plain
                    # `$1` bind bare-compares as text against e.g. a
                    # timestamp/numeric column — Postgres refuses that
                    # outright, and even where it didn't, text ordering
                    # silently disagrees with numeric/date ordering
                    # ("10" < "9"), which would silently drop rows rather
                    # than error. Casting to the column's own type (looked
                    # up from the catalog, not admin input) makes the
                    # comparison exact instead of approximate.
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
                    sql += f' WHERE "{row["cursor_property"]}" > $1::text::{column_type}'
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
