"""Each SQL wire driver reads through a server-side cursor, in batches."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.modules.setdefault("asyncpg", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app import sql_drivers  # noqa: E402

pytestmark = pytest.mark.unit

_ARGS = dict(host="db.example.com", port=1, database="d", username="u", password="p", sql="SELECT 1", args=[])


def _collect(dialect: str, **extra) -> list[list[dict]]:
    async def run():
        return [batch async for batch in sql_drivers.stream_dicts(dialect=dialect, batch_size=2, **{**_ARGS, **extra})]

    return asyncio.run(run())


def test_postgres_reads_a_cursor_inside_a_read_only_transaction() -> None:
    cursor = MagicMock()
    cursor.fetch = AsyncMock(side_effect=[[{"id": 1}, {"id": 2}], [{"id": 3}], []])
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock()
    transaction.__aexit__ = AsyncMock(return_value=False)
    conn = MagicMock()
    conn.transaction.return_value = transaction
    conn.cursor = AsyncMock(return_value=cursor)
    conn.close = AsyncMock()

    with patch.object(sql_drivers.asyncpg, "connect", AsyncMock(return_value=conn)):
        batches = _collect("alloydb")

    assert batches == [[{"id": 1}, {"id": 2}], [{"id": 3}]]
    conn.transaction.assert_called_once_with(readonly=True)
    cursor.fetch.assert_awaited_with(2)
    conn.close.assert_awaited_once()


def test_cockroachdb_reads_whole_then_batches() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"id": 1}, {"id": 2}, {"id": 3}])
    conn.close = AsyncMock()

    with patch.object(sql_drivers.asyncpg, "connect", AsyncMock(return_value=conn)):
        batches = _collect("cockroachdb")

    assert batches == [[{"id": 1}, {"id": 2}], [{"id": 3}]]
    conn.cursor.assert_not_called()


def test_mysql_uses_an_unbuffered_cursor() -> None:
    cursor = MagicMock()
    cursor.execute = AsyncMock()
    cursor.fetchmany = AsyncMock(side_effect=[[{"id": 1}, {"id": 2}], []])
    cursor_cm = MagicMock()
    cursor_cm.__aenter__ = AsyncMock(return_value=cursor)
    cursor_cm.__aexit__ = AsyncMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cursor_cm
    fake_aiomysql = MagicMock()
    fake_aiomysql.connect = AsyncMock(return_value=conn)
    fake_aiomysql.SSDictCursor = object()

    with patch.dict(sys.modules, {"aiomysql": fake_aiomysql}):
        batches = _collect("mariadb")

    assert batches == [[{"id": 1}, {"id": 2}]]
    conn.cursor.assert_called_once_with(fake_aiomysql.SSDictCursor)
    cursor.execute.assert_awaited_once_with("SELECT 1", None)
    conn.close.assert_called_once()


def test_mssql_fetches_many_and_names_columns() -> None:
    cursor = AsyncMock()
    cursor.description = [("id",), ("name",)]
    cursor.fetchmany = AsyncMock(side_effect=[[(1, "a"), (2, "b")], [(3, "c")], []])
    cursor.__aenter__ = AsyncMock(return_value=cursor)
    cursor.__aexit__ = AsyncMock(return_value=None)
    conn = AsyncMock()
    conn.cursor = MagicMock(return_value=cursor)
    fake_aioodbc = MagicMock()
    fake_aioodbc.connect = AsyncMock(return_value=conn)

    with patch.dict(sys.modules, {"aioodbc": fake_aioodbc}):
        batches = _collect("azure_synapse", use_tls=True)

    assert batches == [[{"id": 1, "name": "a"}, {"id": 2, "name": "b"}], [{"id": 3, "name": "c"}]]
    assert "Encryption=require;" in fake_aioodbc.connect.await_args.kwargs["dsn"]
    conn.close.assert_awaited_once()


def test_snowflake_fetches_many_off_the_event_loop() -> None:
    cursor = MagicMock()
    cursor.fetchmany.side_effect = [[{"ID": 1}, {"ID": 2}], []]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    fake_sf = MagicMock()
    fake_sf.connector.connect.return_value = conn
    fake_sf.connector.DictCursor = object()

    with patch.dict(sys.modules, {"snowflake": fake_sf, "snowflake.connector": fake_sf.connector}):
        batches = _collect("snowflake", host="acme-xy12345.snowflakecomputing.com")

    assert batches == [[{"ID": 1}, {"ID": 2}]]
    cursor.fetchmany.assert_called_with(2)
    cursor.close.assert_called_once()
    conn.close.assert_called_once()
