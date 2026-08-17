"""Tests for Intelligence Spend Limits."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.modules.setdefault("asyncpg", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "intelligence"))

from app.groundedness import check_groundedness  # noqa: E402
from app.spend_limits import (  # noqa: E402
    SpendLimitExceeded,
    check_and_increment_request,
    daily_token_quota,
    rpm_limit,
)


def test_rpm_and_quota_env(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_INTELLIGENCE_RPM", "12")
    monkeypatch.setenv("HOLON_INTELLIGENCE_DAILY_TOKEN_QUOTA", "1000")
    assert rpm_limit() == 12
    assert daily_token_quota() == 1000


def test_groundedness_requires_citation_when_context_present() -> None:
    item = SimpleNamespace(urn="hl:acme:main:instance:Customer/1")
    assert check_groundedness("no cite", [item]) is False
    assert check_groundedness("ok [URN: hl:acme:main:instance:Customer/1]", [item]) is True
    assert check_groundedness("bad [URN: hl:acme:main:instance:Customer/999]", [item]) is False


def test_groundedness_empty_context_forbids_invented_urns() -> None:
    assert check_groundedness("I don't know.", []) is True
    assert check_groundedness("see [URN: hl:acme:main:instance:Customer/1]", []) is False


def test_rate_limit_trips_after_rpm(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_INTELLIGENCE_RPM", "2")
    monkeypatch.setenv("HOLON_INTELLIGENCE_DAILY_TOKEN_QUOTA", "0")

    counts = {"n": 0}

    async def fetchval(query, *args):
        counts["n"] += 1
        return counts["n"]

    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=fetchval)

    class _CM:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *_a):
            return False

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_CM())

    async def _body() -> None:
        await check_and_increment_request(pool, tenant_id="acme", principal_urn="hl:acme:global:user:a")
        await check_and_increment_request(pool, tenant_id="acme", principal_urn="hl:acme:global:user:a")
        with pytest.raises(SpendLimitExceeded, match="rate limit"):
            await check_and_increment_request(pool, tenant_id="acme", principal_urn="hl:acme:global:user:a")

    asyncio.run(_body())
