"""Async SQL drivers for the no-code SQL source connector.

postgres → asyncpg, mysql → aiomysql, mssql → aioodbc (FreeTDS).
"""

from __future__ import annotations

from typing import Any, Optional

import asyncpg

VALID_DIALECTS = frozenset({"postgres", "mysql", "mssql"})
DEFAULT_PORTS: dict[str, int] = {
    "postgres": 5432,
    "mysql": 3306,
    "mssql": 1433,
}


def normalize_dialect(dialect: Optional[str]) -> str:
    d = (dialect or "postgres").strip().lower()
    if d not in VALID_DIALECTS:
        raise ValueError(
            f"unsupported dialect {dialect!r} — must be one of {sorted(VALID_DIALECTS)}"
        )
    return d


def default_port_for(dialect: str) -> int:
    return DEFAULT_PORTS[normalize_dialect(dialect)]


def cursor_placeholder(dialect: str, index: int = 1) -> str:
    """Bound-parameter marker for a single incremental-cursor compare."""
    d = normalize_dialect(dialect)
    if d == "mysql":
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
) -> list[dict]:
    """Connect, run one read query, return rows as plain dicts."""
    d = normalize_dialect(dialect)
    if d == "postgres":
        return await _fetch_postgres(host, port, database, username, password, sql, args)
    if d == "mysql":
        return await _fetch_mysql(host, port, database, username, password, sql, args)
    return await _fetch_mssql(host, port, database, username, password, sql, args)


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
