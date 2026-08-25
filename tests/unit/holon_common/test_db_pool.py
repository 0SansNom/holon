"""Tests for Postgres pool size clamping."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))

from holon_common import db  # noqa: E402


def test_min_size_clamped_to_max_size(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_DB_POOL_MIN_SIZE", "10")
    monkeypatch.setenv("HOLON_DB_POOL_MAX_SIZE", "3")
    captured: dict = {}

    async def fake_create_pool(**kwargs):
        captured.update(kwargs)
        return object()

    async def fake_retry(fn, **kwargs):
        return await fn()

    monkeypatch.setattr(db.asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(db, "retry_with_backoff", fake_retry)

    asyncio.run(db.create_pool("postgresql://holon@localhost/holon"))
    assert captured["min_size"] == 3
    assert captured["max_size"] == 3
