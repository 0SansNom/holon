"""REST pagination keeps going past 100 pages and yields one page at a time."""

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

from app.generic_rest_fetch import fetch_for_dataset  # noqa: E402

pytestmark = pytest.mark.unit


def test_rest_fetch_yields_every_page_past_the_old_cap() -> None:
    async def scenario() -> None:
        row = {
            "base_url": "https://example.test/items",
            "auth_header_name": None,
            "auth_header_value": None,
            "secret_ref": None,
            "connection_name": None,
            "record_path": "items",
            "next_page_path": "next",
            "cursor_property": None,
            "incremental_param": None,
            "last_cursor_value": None,
            "cursor_boundary_keys": None,
        }
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=row)

        class _Response:
            def __init__(self, page: int) -> None:
                self._page = page

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                nxt = None if self._page == 101 else f"https://example.test/items?page={self._page + 1}"
                return {"items": [{"id": self._page}], "next": nxt}

        class _Client:
            def __init__(self, *args, **kwargs) -> None:
                self.page = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def get(self, url, headers):
                self.page += 1
                return _Response(self.page)

        with patch("app.generic_rest_fetch.httpx.AsyncClient", _Client), patch(
            "app.generic_rest_fetch.pinned_transport", lambda: None
        ), patch("app.generic_rest_fetch.assert_http_url", lambda url: None):
            batches, commit = await fetch_for_dataset(pool, "acme", "items")
            seen = []
            async for batch in batches:
                seen.extend(batch)
        assert commit is None
        assert [row["id"] for row in seen] == list(range(1, 102))

    asyncio.run(scenario())
