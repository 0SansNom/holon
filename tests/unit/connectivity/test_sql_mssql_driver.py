"""Unit / smoke tests for the MSSQL (aioodbc) driver path.

Full MSSQL image is too heavy for default CI — keep an optional compose
profile note for a later smoke against a real server. Here we only assert
DSN construction and that the fetch entrypoint wires the mocked driver.
"""

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


def test_mssql_fetch_dicts_uses_freetds_dsn() -> None:
    """Smoke: aioodbc.connect receives a FreeTDS DSN (no real SQL Server).

    Optional live profile (not enabled by default): docker compose service
    based on mcr.microsoft.com/mssql/server with dialect=mssql registration.
    """

    async def _run() -> None:
        cursor = AsyncMock()
        cursor.description = [("id",), ("name",)]
        cursor.fetchall = AsyncMock(return_value=[(1, "acme")])
        cursor.__aenter__ = AsyncMock(return_value=cursor)
        cursor.__aexit__ = AsyncMock(return_value=None)

        conn = AsyncMock()
        conn.cursor = MagicMock(return_value=cursor)
        conn.close = AsyncMock()

        fake_aioodbc = MagicMock()
        fake_aioodbc.connect = AsyncMock(return_value=conn)

        with patch.dict(sys.modules, {"aioodbc": fake_aioodbc}):
            rows = await sql_drivers.fetch_dicts(
                dialect="mssql",
                host="sqlserver",
                port=1433,
                database="erp",
                username="sa",
                password="secret",
                sql="SELECT id, name FROM orders WHERE id > ?",
                args=["0"],
            )

        assert rows == [{"id": 1, "name": "acme"}]
        dsn = fake_aioodbc.connect.await_args.kwargs["dsn"]
        assert "DRIVER={FreeTDS}" in dsn
        assert "SERVER=sqlserver" in dsn
        assert "PORT=1433" in dsn
        assert "DATABASE=erp" in dsn
        cursor.execute.assert_awaited_once_with(
            "SELECT id, name FROM orders WHERE id > ?", ["0"]
        )

    asyncio.run(_run())
