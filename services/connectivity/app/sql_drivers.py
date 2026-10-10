"""Async SQL drivers for the no-code SQL source connector.

Wire protocols: postgres → asyncpg, mysql → aiomysql, mssql → aioodbc
(FreeTDS), snowflake → snowflake-connector-python (sync, worker thread).

Compatible products (AlloyDB, CockroachDB, …) are named dialects that map
onto those four wire drivers — same guard, quoting, and bind style.
"""

from __future__ import annotations

import asyncio
import re
import ssl
from typing import Any, AsyncIterator, Optional

import asyncpg

# Stored dialect name → wire driver / quoting / bind style.
DIALECT_WIRE: dict[str, str] = {
    "postgres": "postgres",
    "alloydb": "postgres",
    "cockroachdb": "postgres",
    "enterprisedb": "postgres",
    "greenplum": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
    "singlestore": "mysql",
    "mssql": "mssql",
    "azure_synapse": "mssql",
    "azure_synapse_serverless": "mssql",
    "snowflake": "snowflake",
}
VALID_DIALECTS = frozenset(DIALECT_WIRE)
WIRE_DIALECTS = frozenset(DIALECT_WIRE.values())

DEFAULT_PORTS: dict[str, int] = {
    "postgres": 5432,
    "alloydb": 5432,
    "cockroachdb": 26257,
    "enterprisedb": 5444,
    "greenplum": 5432,
    "mysql": 3306,
    "mariadb": 3306,
    "singlestore": 3306,
    "mssql": 1433,
    "azure_synapse": 1433,
    "azure_synapse_serverless": 1433,
    "snowflake": 443,
}

# Products that refuse cleartext. Omitted `use_tls` on register uses this set.
# Snowflake is already HTTPS and is not listed — the flag is ignored for it.
TLS_BY_DEFAULT = frozenset({
    "alloydb",
    "cockroachdb",
    "azure_synapse",
    "azure_synapse_serverless",
})

_SNOWFLAKE_HOST_SUFFIX = ".snowflakecomputing.com"
_SNOWFLAKE_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def normalize_dialect(dialect: Optional[str]) -> str:
    """Canonical stored dialect name (product alias or wire name)."""
    d = (dialect or "postgres").strip().lower().replace("-", "_").replace(" ", "_")
    if d not in VALID_DIALECTS:
        raise ValueError(
            f"unsupported dialect {dialect!r} — must be one of {sorted(VALID_DIALECTS)}"
        )
    return d


def wire_dialect(dialect: str) -> str:
    """Wire protocol used for drivers, quoting, and cursor binds."""
    return DIALECT_WIRE[normalize_dialect(dialect)]


def default_port_for(dialect: str) -> int:
    return DEFAULT_PORTS[normalize_dialect(dialect)]


def default_tls_for(dialect: str) -> bool:
    """Whether a new connection should encrypt when the client omits `use_tls`."""
    return normalize_dialect(dialect) in TLS_BY_DEFAULT


def resolve_use_tls(dialect: str, use_tls: Optional[bool]) -> bool:
    """Stored TLS flag. Explicit false stays false (local proxy). Snowflake is always false."""
    stored = normalize_dialect(dialect)
    if wire_dialect(stored) == "snowflake":
        return False
    if use_tls is None:
        return stored in TLS_BY_DEFAULT
    return bool(use_tls)


def normalize_snowflake_host(host: str) -> str:
    """Expand an account locator to the public Snowflake HTTPS hostname.

    Accepts either the account id (`xy12345.eu-central-1`) or the full
    `*.snowflakecomputing.com` FQDN. Returns the FQDN used for SSRF checks.
    """
    raw = (host or "").strip()
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    raw = raw.split("/", 1)[0].rstrip(".").lower()
    if not raw:
        raise ValueError("Snowflake host / account locator is required")
    if raw.endswith(_SNOWFLAKE_HOST_SUFFIX):
        account = raw[: -len(_SNOWFLAKE_HOST_SUFFIX)]
    else:
        account = raw
        raw = f"{account}{_SNOWFLAKE_HOST_SUFFIX}"
    if not _SNOWFLAKE_ACCOUNT_RE.match(account):
        raise ValueError(
            f"invalid Snowflake account locator {account!r} — use the account id "
            "(e.g. 'xy12345' or 'xy12345.eu-central-1')"
        )
    return raw


def snowflake_account_from_host(host: str) -> str:
    """Account id passed to snowflake.connector.connect(account=...)."""
    normalized = normalize_snowflake_host(host)
    return normalized[: -len(_SNOWFLAKE_HOST_SUFFIX)]


def cursor_placeholder(dialect: str, index: int = 1) -> str:
    """Bound-parameter marker for a single incremental-cursor compare."""
    d = wire_dialect(dialect)
    if d in {"mysql", "snowflake"}:
        return "%s"
    if d == "mssql":
        return "?"
    return f"${index}"


async def fetch_dicts(
    *,
    dialect: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: Optional[str],
    sql: str,
    args: list[Any],
    warehouse: Optional[str] = None,
    use_tls: bool = False,
) -> list[dict]:
    """Connect, run one read query, return rows as plain dicts."""
    d = wire_dialect(dialect)
    if d == "postgres":
        return await _fetch_postgres(
            host, port, database, username, password, sql, args, use_tls=use_tls
        )
    if d == "mysql":
        return await _fetch_mysql(
            host, port, database, username, password, sql, args, use_tls=use_tls
        )
    if d == "mssql":
        return await _fetch_mssql(
            host, port, database, username, password, sql, args, use_tls=use_tls
        )
    return await asyncio.to_thread(
        _fetch_snowflake_sync,
        host=host,
        database=database,
        username=username,
        password=password,
        warehouse=warehouse,
        sql=sql,
        args=args,
    )


async def stream_dicts(
    *,
    dialect: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: Optional[str],
    sql: str,
    args: list[Any],
    batch_size: int,
    warehouse: Optional[str] = None,
    use_tls: bool = False,
) -> AsyncIterator[list[dict]]:
    """Run one read query and yield its rows in batches of at most `batch_size`.

    Each driver reads through a server-side cursor, so the result set is never
    held in memory whole. CockroachDB is the exception: pausing a pgwire portal
    there is a preview feature, off by default, so its rows are read whole.
    """
    d = wire_dialect(dialect)
    if normalize_dialect(dialect) == "cockroachdb":
        rows = await _fetch_postgres(host, port, database, username, password, sql, args, use_tls=use_tls)
        for start in range(0, len(rows), batch_size):
            yield rows[start:start + batch_size]
        return
    if d == "postgres":
        stream = _stream_postgres(host, port, database, username, password, sql, args, batch_size, use_tls)
    elif d == "mysql":
        stream = _stream_mysql(host, port, database, username, password, sql, args, batch_size, use_tls)
    elif d == "mssql":
        stream = _stream_mssql(host, port, database, username, password, sql, args, batch_size, use_tls)
    else:
        stream = _stream_snowflake(
            host=host, database=database, username=username, password=password,
            warehouse=warehouse, sql=sql, args=args, batch_size=batch_size,
        )
    async for batch in stream:
        yield batch


async def _connect_postgres(host, port, database, username, password, use_tls):
    return await asyncpg.connect(
        host=host,
        port=port,
        database=database,
        user=username,
        password=password,
        timeout=15.0,
        # True uses the default trust store. No private CA in this connector.
        ssl=True if use_tls else None,
    )


async def _fetch_postgres(
    host: str,
    port: int,
    database: str,
    username: str,
    password: Optional[str],
    sql: str,
    args: list[Any],
    use_tls: bool = False,
) -> list[dict]:
    conn = await _connect_postgres(host, port, database, username, password, use_tls)
    try:
        records = await conn.fetch(sql, *args)
        return [dict(record) for record in records]
    finally:
        await conn.close()


async def _stream_postgres(host, port, database, username, password, sql, args, batch_size, use_tls):
    conn = await _connect_postgres(host, port, database, username, password, use_tls)
    try:
        # Portals only live inside a transaction; read-only also bars side effects.
        async with conn.transaction(readonly=True):
            cursor = await conn.cursor(sql, *args)
            while records := await cursor.fetch(batch_size):
                yield [dict(record) for record in records]
    finally:
        await conn.close()


async def _connect_mysql(host, port, database, username, password, use_tls):
    import aiomysql

    return await aiomysql.connect(
        host=host,
        port=port,
        db=database,
        user=username,
        password=password or "",
        connect_timeout=15,
        autocommit=True,
        ssl=ssl.create_default_context() if use_tls else None,
    )


async def _fetch_mysql(
    host: str,
    port: int,
    database: str,
    username: str,
    password: Optional[str],
    sql: str,
    args: list[Any],
    use_tls: bool = False,
) -> list[dict]:
    import aiomysql

    conn = await _connect_mysql(host, port, database, username, password, use_tls)
    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # None skips pyformat interpolation, so a literal % in a custom query survives.
            await cur.execute(sql, args or None)
            rows = await cur.fetchall()
            return [dict(row) for row in rows]
    finally:
        conn.close()


async def _stream_mysql(host, port, database, username, password, sql, args, batch_size, use_tls):
    import aiomysql

    conn = await _connect_mysql(host, port, database, username, password, use_tls)
    try:
        # Unbuffered cursor: rows arrive as they are read instead of all at execute().
        async with conn.cursor(aiomysql.SSDictCursor) as cur:
            await cur.execute(sql, args or None)
            while rows := await cur.fetchmany(batch_size):
                yield [dict(row) for row in rows]
    finally:
        conn.close()


async def _connect_mssql(host, port, database, username, password, use_tls):
    import aioodbc

    # FreeTDS is registered in the Connectivity image (see Dockerfile).
    encryption = "Encryption=require;" if use_tls else ""
    dsn = (
        f"DRIVER={{FreeTDS}};"
        f"SERVER={host};"
        f"PORT={port};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password or ''};"
        "TDS_Version=7.4;"
        f"{encryption}"
    )
    return await aioodbc.connect(dsn=dsn, timeout=15)


async def _fetch_mssql(
    host: str,
    port: int,
    database: str,
    username: str,
    password: Optional[str],
    sql: str,
    args: list[Any],
    use_tls: bool = False,
) -> list[dict]:
    conn = await _connect_mssql(host, port, database, username, password, use_tls)
    try:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            if cur.description is None:
                return []
            columns = [col[0] for col in cur.description]
            rows = await cur.fetchall()
            return [dict(zip(columns, row)) for row in rows]
    finally:
        await conn.close()


async def _stream_mssql(host, port, database, username, password, sql, args, batch_size, use_tls):
    conn = await _connect_mssql(host, port, database, username, password, use_tls)
    try:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            if cur.description is None:
                return
            columns = [col[0] for col in cur.description]
            while rows := await cur.fetchmany(batch_size):
                yield [dict(zip(columns, row)) for row in rows]
    finally:
        await conn.close()


def _connect_snowflake(*, host, database, username, password, warehouse):
    import snowflake.connector

    connect_kwargs: dict[str, Any] = {
        "user": username,
        "password": password or "",
        "account": snowflake_account_from_host(host),
        "database": database,
        "login_timeout": 15,
        "network_timeout": 30,
    }
    if warehouse and str(warehouse).strip():
        connect_kwargs["warehouse"] = str(warehouse).strip()
    return snowflake.connector.connect(**connect_kwargs)


def _fetch_snowflake_sync(
    *,
    host: str,
    database: str,
    username: str,
    password: Optional[str],
    warehouse: Optional[str],
    sql: str,
    args: list[Any],
) -> list[dict]:
    import snowflake.connector

    conn = _connect_snowflake(
        host=host, database=database, username=username, password=password, warehouse=warehouse
    )
    try:
        cur = conn.cursor(snowflake.connector.DictCursor)
        try:
            # None skips pyformat interpolation, so a literal % in a custom query survives.
            cur.execute(sql, args or None)
            rows = cur.fetchall()
            return [dict(row) for row in rows]
        finally:
            cur.close()
    finally:
        conn.close()


async def _stream_snowflake(*, host, database, username, password, warehouse, sql, args, batch_size):
    import snowflake.connector

    # The connector is synchronous: every blocking call runs in a worker thread.
    conn = await asyncio.to_thread(
        _connect_snowflake,
        host=host, database=database, username=username, password=password, warehouse=warehouse,
    )
    try:
        cur = conn.cursor(snowflake.connector.DictCursor)
        try:
            await asyncio.to_thread(cur.execute, sql, args or None)
            while rows := await asyncio.to_thread(cur.fetchmany, batch_size):
                yield [dict(row) for row in rows]
        finally:
            await asyncio.to_thread(cur.close)
    finally:
        await asyncio.to_thread(conn.close)
