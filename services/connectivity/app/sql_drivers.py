"""Async SQL drivers for the no-code SQL source connector.

postgres → asyncpg, mysql → aiomysql, mssql → aioodbc (FreeTDS),
snowflake → snowflake-connector-python (sync, run in a worker thread).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Optional

import asyncpg

VALID_DIALECTS = frozenset({"postgres", "mysql", "mssql", "snowflake"})
DEFAULT_PORTS: dict[str, int] = {
    "postgres": 5432,
    "mysql": 3306,
    "mssql": 1433,
    "snowflake": 443,
}

_SNOWFLAKE_HOST_SUFFIX = ".snowflakecomputing.com"
_SNOWFLAKE_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def normalize_dialect(dialect: Optional[str]) -> str:
    d = (dialect or "postgres").strip().lower()
    if d not in VALID_DIALECTS:
        raise ValueError(
            f"unsupported dialect {dialect!r} — must be one of {sorted(VALID_DIALECTS)}"
        )
    return d


def default_port_for(dialect: str) -> int:
    return DEFAULT_PORTS[normalize_dialect(dialect)]


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
    d = normalize_dialect(dialect)
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
) -> list[dict]:
    """Connect, run one read query, return rows as plain dicts."""
    d = normalize_dialect(dialect)
    if d == "postgres":
        return await _fetch_postgres(host, port, database, username, password, sql, args)
    if d == "mysql":
        return await _fetch_mysql(host, port, database, username, password, sql, args)
    if d == "mssql":
        return await _fetch_mssql(host, port, database, username, password, sql, args)
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


async def _fetch_postgres(
    host: str,
    port: int,
    database: str,
    username: str,
    password: Optional[str],
    sql: str,
    args: list[Any],
) -> list[dict]:
    conn = await asyncpg.connect(
        host=host,
        port=port,
        database=database,
        user=username,
        password=password,
        timeout=15.0,
    )
    try:
        records = await conn.fetch(sql, *args)
        return [dict(record) for record in records]
    finally:
        await conn.close()


async def _fetch_mysql(
    host: str,
    port: int,
    database: str,
    username: str,
    password: Optional[str],
    sql: str,
    args: list[Any],
) -> list[dict]:
    import aiomysql

    conn = await aiomysql.connect(
        host=host,
        port=port,
        db=database,
        user=username,
        password=password or "",
        connect_timeout=15,
        autocommit=True,
    )
    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, args or None)
            rows = await cur.fetchall()
            return [dict(row) for row in rows]
    finally:
        conn.close()


async def _fetch_mssql(
    host: str,
    port: int,
    database: str,
    username: str,
    password: Optional[str],
    sql: str,
    args: list[Any],
) -> list[dict]:
    import aioodbc

    # FreeTDS is registered in the Connectivity image (see Dockerfile).
    dsn = (
        f"DRIVER={{FreeTDS}};"
        f"SERVER={host};"
        f"PORT={port};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password or ''};"
        "TDS_Version=7.4;"
    )
    conn = await aioodbc.connect(dsn=dsn, timeout=15)
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

    conn = snowflake.connector.connect(**connect_kwargs)
    try:
        cur = conn.cursor(snowflake.connector.DictCursor)
        try:
            cur.execute(sql, args or None)
            rows = cur.fetchall()
            return [dict(row) for row in rows]
        finally:
            cur.close()
    finally:
        conn.close()
