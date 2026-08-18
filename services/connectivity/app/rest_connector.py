"""REST connector — HTTP REST API source ingestion."""

from __future__ import annotations

import httpx


async def read_reviews(reviews_api_url: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        response = await client.get(reviews_api_url, timeout=10)
        response.raise_for_status()
    return response.json()
