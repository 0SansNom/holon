"""Unit smoke for Intelligence evaluation gold set (no live LLM)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

REPO = Path(__file__).resolve().parents[3]

sys.modules.setdefault("asyncpg", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "intelligence"))

from app.gold_set import (  # noqa: E402
    STARTER_GOLD_SET,
    gold_set_disclaimer,
    seed_starter_gold_set,
)


def test_starter_gold_set_has_at_least_ten_questions() -> None:
    assert len(STARTER_GOLD_SET) >= 10
    categories = {c for _, c, _ in STARTER_GOLD_SET}
    assert "ontology" in categories
    assert "actions" in categories


def test_gold_set_disclaimer_mentions_starter() -> None:
    text = gold_set_disclaimer(seeded_starter=True)
    assert "starter" in text.lower()


def test_seed_starter_gold_set_inserts_when_empty() -> None:
    async def _run() -> None:
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=0)
        conn.execute = AsyncMock()
        inserted = await seed_starter_gold_set(conn)
        assert inserted == len(STARTER_GOLD_SET)
        assert conn.execute.await_count == len(STARTER_GOLD_SET)

    asyncio.run(_run())


def test_seed_starter_gold_set_is_idempotent_when_populated() -> None:
    async def _run() -> None:
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=12)
        conn.execute = AsyncMock()
        inserted = await seed_starter_gold_set(conn)
        assert inserted == 0
        conn.execute.assert_not_awaited()

    asyncio.run(_run())
