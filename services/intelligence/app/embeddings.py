"""Embedding Client — feeds the semantic retrieval channel (metadata only,
never bulk instance vectorization).

Two real backends (Voyage API or local sentence-transformers) selected at startup by
`HOLON_EMBEDDING_PROVIDER` — callers see the `EmbeddingClient`
protocol regardless of backend.
"""

from __future__ import annotations

import asyncio
import os
from typing import Protocol

import voyageai
from sentence_transformers import SentenceTransformer

from holon_common import CircuitBreaker, retry_with_backoff

_LOCAL_MODEL_NAME = "all-MiniLM-L6-v2"
_VOYAGE_MODEL_NAME = "voyage-3.5-lite"  # small/cheap — this build only ever embeds metadata, not bulk instances


class EmbeddingClient(Protocol):
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class VoyageEmbeddingClient:
    dimension = 1024  # voyage-3.5-lite's native output dimension

    def __init__(self, api_key: str):
        self._client = voyageai.AsyncClient(api_key=api_key)
        self._breaker = CircuitBreaker(name="voyage-embed", failure_threshold=5, cooldown_seconds=30.0)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async def _do() -> list[list[float]]:
            result = await retry_with_backoff(
                lambda: self._client.embed(texts, model=_VOYAGE_MODEL_NAME, input_type="document"),
                attempts=3,
                base_delay=2.0,
                what="Voyage embedding",
            )
            return result.embeddings

        return await self._breaker.call(_do)


class LocalEmbeddingClient:
    dimension = 384  # all-MiniLM-L6-v2's native output dimension

    def __init__(self):
        # Loaded once at startup (see main.py's lifespan) — the model
        # download/load is the slow part, not each individual embed call.
        self._model = SentenceTransformer(_LOCAL_MODEL_NAME)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # sentence-transformers is synchronous — thread-hop it, same
        # pattern used for pyiceberg/DuckDB elsewhere in this build
        # (blocking the event loop here would starve every other
        # concurrent request).
        embeddings = await asyncio.to_thread(self._model.encode, texts)
        return [vector.tolist() for vector in embeddings]


def build_embedding_client() -> EmbeddingClient:
    provider = os.environ.get("HOLON_EMBEDDING_PROVIDER", "voyage")
    if provider == "voyage":
        return VoyageEmbeddingClient(os.environ["VOYAGE_API_KEY"])
    if provider == "local":
        return LocalEmbeddingClient()
    raise ValueError(f"unknown HOLON_EMBEDDING_PROVIDER: {provider!r} (must be 'voyage' or 'local')")
