"""Literal `%` in custom queries must reach pyformat drivers unformatted."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

REPO = Path(__file__).resolve().parents[3]
sys.modules.setdefault("asyncpg", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app import sql_drivers  # noqa: E402

LIKE_QUERY = "SELECT id FROM orders WHERE name LIKE 'a%'"


def _fetch(dialect: str) -> None:
    asyncio.run(
        sql_drivers.fetch_dicts(
            dialect=dialect,
            host="xy12345.snowflakecomputing.com",
            port=3306,
            database="db",
            username="u",
            password="p",
            warehouse=None,
            sql=LIKE_QUERY,
            args=[],
        )
    )


def test_mysql_custom_query_without_args_is_not_formatted() -> None:
    cursor = MagicMock()
    cursor.execute = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=[])
    cursor_cm = MagicMock()
    cursor_cm.__aenter__ = AsyncMock(return_value=cursor)
    cursor_cm.__aexit__ = AsyncMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cursor_cm

    fake_aiomysql = MagicMock()
    fake_aiomysql.connect = AsyncMock(return_value=conn)

    with patch.dict(sys.modules, {"aiomysql": fake_aiomysql}):
        _fetch("mysql")

    cursor.execute.assert_awaited_once_with(LIKE_QUERY, None)


def test_snowflake_custom_query_without_args_is_not_formatted() -> None:
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cursor

    fake_sf = MagicMock()
    fake_sf.connector.connect.return_value = conn

    with patch.dict(sys.modules, {"snowflake": fake_sf, "snowflake.connector": fake_sf.connector}):
        _fetch("snowflake")

    cursor.execute.assert_called_once_with(LIKE_QUERY, None)
