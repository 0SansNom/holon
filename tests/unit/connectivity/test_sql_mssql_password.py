"""A semicolon in an MSSQL password stays inside the password."""

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


def test_password_semicolon_and_brace_stay_in_the_value() -> None:
    async def _run() -> None:
        cursor = AsyncMock()
        cursor.description = None
        cursor.__aenter__ = AsyncMock(return_value=cursor)
        cursor.__aexit__ = AsyncMock(return_value=None)
        conn = AsyncMock()
        conn.cursor = MagicMock(return_value=cursor)
        conn.close = AsyncMock()
        fake_aioodbc = MagicMock()
        fake_aioodbc.connect = AsyncMock(return_value=conn)

        with patch.dict(sys.modules, {"aioodbc": fake_aioodbc}):
            await sql_drivers.fetch_dicts(
                dialect="mssql",
                host="sql;server",
                port=1433,
                database="erp",
                username="sa",
                password="p@ss;wo}rd",
                sql="SELECT 1",
                args=[],
                use_tls=True,
            )

        dsn = fake_aioodbc.connect.await_args.kwargs["dsn"]
        assert "PWD={p@ss;wo}}rd};" in dsn
        assert "SERVER={sql;server};" in dsn
        assert "Encryption=require;" in dsn

    asyncio.run(_run())
