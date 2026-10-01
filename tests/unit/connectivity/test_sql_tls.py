"""TLS is passed through to the postgres and mysql drivers."""

from __future__ import annotations

import asyncio
import ssl
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

REPO = Path(__file__).resolve().parents[3]
sys.modules.setdefault("asyncpg", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app import sql_drivers  # noqa: E402


def test_postgres_use_tls_sets_asyncpg_ssl() -> None:
    async def _run() -> None:
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        conn.close = AsyncMock()
        with patch.object(sql_drivers.asyncpg, "connect", AsyncMock(return_value=conn)) as connect:
            await sql_drivers.fetch_dicts(
                dialect="alloydb",
                host="alloy.example",
                port=5432,
                database="db",
                username="u",
                password="p",
                sql="SELECT 1",
                args=[],
                use_tls=True,
            )
        assert connect.await_args.kwargs["ssl"] is True

    asyncio.run(_run())


def test_mysql_use_tls_sets_ssl_context() -> None:
    async def _run() -> None:
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.fetchall = AsyncMock(return_value=[])
        cursor_cm = MagicMock()
        cursor_cm.__aenter__ = AsyncMock(return_value=cursor)
        cursor_cm.__aexit__ = AsyncMock(return_value=False)
        conn = MagicMock()
        conn.cursor.return_value = cursor_cm
        conn.close = MagicMock()
        fake_aiomysql = MagicMock()
        fake_aiomysql.connect = AsyncMock(return_value=conn)
        fake_aiomysql.DictCursor = object()

        with patch.dict(sys.modules, {"aiomysql": fake_aiomysql}):
            await sql_drivers.fetch_dicts(
                dialect="singlestore",
                host="svc.singlestore.com",
                port=3306,
                database="db",
                username="u",
                password="p",
                sql="SELECT 1",
                args=[],
                use_tls=True,
            )

        assert isinstance(fake_aiomysql.connect.await_args.kwargs["ssl"], ssl.SSLContext)

    asyncio.run(_run())
