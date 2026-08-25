from __future__ import annotations

import os

import asyncpg

from .observability import retry_with_backoff


def _pool_size_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


async def create_pool(dsn: str) -> asyncpg.Pool:
    min_size = _pool_size_env("HOLON_DB_POOL_MIN_SIZE", 1)
    max_size = _pool_size_env("HOLON_DB_POOL_MAX_SIZE", 5)
    if min_size > max_size:
        min_size = max_size
    return await retry_with_backoff(
        lambda: asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size),
        attempts=10,
        base_delay=1.5,
        what="Postgres pool creation",
    )
