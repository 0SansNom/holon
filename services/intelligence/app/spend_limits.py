"""Basic spend controls for Intelligence — RPM per principal + daily token quota per tenant.

Postgres-backed so multiple replicas share the same counters. Env:

  HOLON_INTELLIGENCE_RPM                 default 30 (0 = unlimited)
  HOLON_INTELLIGENCE_DAILY_TOKEN_QUOTA   default 200000 (0 = unlimited)
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

import asyncpg

DDL = """
CREATE TABLE IF NOT EXISTS intelligence_request_window (
    principal_urn TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    request_count INT NOT NULL DEFAULT 0,
    PRIMARY KEY (principal_urn, window_start)
);

CREATE TABLE IF NOT EXISTS intelligence_token_day (
    tenant_id TEXT NOT NULL,
    day DATE NOT NULL,
    tokens BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, day)
);
"""


class SpendLimitExceeded(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def _int_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def rpm_limit() -> int:
    return _int_env("HOLON_INTELLIGENCE_RPM", 30)


def daily_token_quota() -> int:
    return _int_env("HOLON_INTELLIGENCE_DAILY_TOKEN_QUOTA", 200_000)


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


def _minute_floor(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(second=0, microsecond=0)


async def check_and_increment_request(
    pool: asyncpg.Pool, *, tenant_id: str, principal_urn: str
) -> None:
    limit = rpm_limit()
    if limit <= 0:
        return
    window = _minute_floor()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """
            INSERT INTO intelligence_request_window (principal_urn, tenant_id, window_start, request_count)
            VALUES ($1, $2, $3, 1)
            ON CONFLICT (principal_urn, window_start)
            DO UPDATE SET request_count = intelligence_request_window.request_count + 1
            RETURNING request_count
            """,
            principal_urn,
            tenant_id,
            window,
        )
    if int(count) > limit:
        raise SpendLimitExceeded(
            f"rate limit exceeded: {limit} requests/minute for principal (HOLON_INTELLIGENCE_RPM)"
        )


async def check_daily_token_quota(pool: asyncpg.Pool, *, tenant_id: str) -> None:
    quota = daily_token_quota()
    if quota <= 0:
        return
    today = date.today()
    async with pool.acquire() as conn:
        used = await conn.fetchval(
            "SELECT tokens FROM intelligence_token_day WHERE tenant_id = $1 AND day = $2",
            tenant_id,
            today,
        )
    if used is not None and int(used) >= quota:
        raise SpendLimitExceeded(
            f"daily token quota exceeded: {quota} tokens/tenant/day (HOLON_INTELLIGENCE_DAILY_TOKEN_QUOTA)"
        )


async def record_tokens(pool: asyncpg.Pool, *, tenant_id: str, tokens: int) -> None:
    if tokens <= 0:
        return
    quota = daily_token_quota()
    today = date.today()
    async with pool.acquire() as conn:
        new_total = await conn.fetchval(
            """
            INSERT INTO intelligence_token_day (tenant_id, day, tokens)
            VALUES ($1, $2, $3)
            ON CONFLICT (tenant_id, day)
            DO UPDATE SET tokens = intelligence_token_day.tokens + EXCLUDED.tokens
            RETURNING tokens
            """,
            tenant_id,
            today,
            tokens,
        )
    if quota > 0 and int(new_total) > quota:
        # Soft note: request already spent; next call will 429. Avoid rolling back LLM spend.
        pass


async def enforce_before_spend(pool: asyncpg.Pool, *, tenant_id: str, principal_urn: str) -> None:
    await check_and_increment_request(pool, tenant_id=tenant_id, principal_urn=principal_urn)
    await check_daily_token_quota(pool, tenant_id=tenant_id)
