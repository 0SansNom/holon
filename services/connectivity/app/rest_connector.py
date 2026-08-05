"""The generic REST connector: a source reached over
plain HTTP rather than a database driver.

Unlike `connector.py` (asyncpg, natively async) and `mongo_connector.py`
(pymongo, synchronous — wrapped in `asyncio.to_thread` at the call site),
`httpx` is natively async, so this is the first reader that needs neither
a special driver nor a thread hop. Three connectors, three different
concurrency shapes, all handled correctly — that variety is the point of
this increment as much as the new data is.
"""

from __future__ import annotations

import httpx


async def read_reviews(reviews_api_url: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        response = await client.get(reviews_api_url, timeout=10)
        response.raise_for_status()
    return response.json()
