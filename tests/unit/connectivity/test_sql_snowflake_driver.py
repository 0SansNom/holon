"""Unit / smoke tests for the Snowflake SQL driver path.

No live Snowflake account in CI — mock snowflake.connector and assert
account/host normalization plus connect kwargs wiring.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.modules.setdefault("asyncpg", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app import sql_drivers  # noqa: E402


def test_normalize_snowflake_host_expands_account() -> None:
    assert (
        sql_drivers.normalize_snowflake_host("xy12345.eu-central-1")
        == "xy12345.eu-central-1.snowflakecomputing.com"
    )
    assert (
        sql_drivers.normalize_snowflake_host("xy12345.eu-central-1.snowflakecomputing.com")
        == "xy12345.eu-central-1.snowflakecomputing.com"
    )
    assert sql_drivers.snowflake_account_from_host("xy12345") == "xy12345"


def test_normalize_snowflake_host_rejects_junk() -> None:
    with pytest.raises(ValueError):
        sql_drivers.normalize_snowflake_host("")
    with pytest.raises(ValueError):
        sql_drivers.normalize_snowflake_host("bad host!")


def test_snowflake_fetch_dicts_uses_connector() -> None:
    async def _run() -> None:
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"id": 1, "name": "acme"}]
        cursor.close = MagicMock()

        conn = MagicMock()
        conn.cursor.return_value = cursor
        conn.close = MagicMock()

        fake_sf = MagicMock()
        fake_sf.connector.connect.return_value = conn
        fake_sf.connector.DictCursor = object()

        with patch.dict(sys.modules, {"snowflake": fake_sf, "snowflake.connector": fake_sf.connector}):
            rows = await sql_drivers.fetch_dicts(
                dialect="snowflake",
                host="xy12345.eu-central-1",
                port=443,
                database="ANALYTICS",
                username="reader",
                password="secret",
                warehouse="COMPUTE_WH",
                sql="SELECT id, name FROM orders WHERE id > %s",
                args=[0],
            )

        assert rows == [{"id": 1, "name": "acme"}]
        kwargs = fake_sf.connector.connect.call_args.kwargs
        assert kwargs["account"] == "xy12345.eu-central-1"
        assert kwargs["database"] == "ANALYTICS"
        assert kwargs["user"] == "reader"
        assert kwargs["password"] == "secret"
        assert kwargs["warehouse"] == "COMPUTE_WH"
        cursor.execute.assert_called_once_with(
            "SELECT id, name FROM orders WHERE id > %s", [0]
        )

    asyncio.run(_run())


def test_snowflake_fetch_omits_empty_warehouse() -> None:
    async def _run() -> None:
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        cursor.close = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cursor
        conn.close = MagicMock()

        fake_sf = MagicMock()
        fake_sf.connector.connect.return_value = conn
        fake_sf.connector.DictCursor = object()

        with patch.dict(sys.modules, {"snowflake": fake_sf, "snowflake.connector": fake_sf.connector}):
            await sql_drivers.fetch_dicts(
                dialect="snowflake",
                host="xy12345.snowflakecomputing.com",
                port=443,
                database="DB",
                username="u",
                password="p",
                warehouse="  ",
                sql="SELECT 1",
                args=[],
            )

        kwargs = fake_sf.connector.connect.call_args.kwargs
        assert "warehouse" not in kwargs

    asyncio.run(_run())
