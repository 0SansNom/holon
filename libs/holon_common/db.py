from __future__ import annotations

import asyncpg

from .observability import retry_with_backoff


async def create_pool(dsn: str) -> asyncpg.Pool:
    return await retry_with_backoff(
        lambda: asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5),
        attempts=10,
        base_delay=1.5,
        what="Postgres pool creation",
    )
