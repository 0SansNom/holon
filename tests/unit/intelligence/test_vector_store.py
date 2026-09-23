"""Unit tests for Intelligence Qdrant vector store tenant scoping."""

from __future__ import annotations

import asyncio
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

# Host unit runs may lack intelligence service deps — stub before importing.
for _name, _attrs in (
    ("qdrant_client", {"AsyncQdrantClient": MagicMock}),
    (
        "qdrant_client.models",
        {
            "Distance": object,
            "FieldCondition": None,  # filled below
            "Filter": None,
            "MatchValue": None,
            "PointStruct": MagicMock,
            "VectorParams": MagicMock,
        },
    ),
    ("voyageai", {"AsyncClient": MagicMock}),
    ("sentence_transformers", {"SentenceTransformer": MagicMock}),
):
    if _name not in sys.modules:
        mod = types.ModuleType(_name)
        for k, v in _attrs.items():
            setattr(mod, k, v)
        sys.modules[_name] = mod

# Real Filter helpers used by semantic_search assertions
class _Filter:
    def __init__(self, must=None):
        self.must = must or []


class _FieldCondition:
    def __init__(self, key, match=None):
        self.key = key
        self.match = match


class _MatchValue:
    def __init__(self, value):
        self.value = value


sys.modules["qdrant_client.models"].Filter = _Filter
sys.modules["qdrant_client.models"].FieldCondition = _FieldCondition
sys.modules["qdrant_client.models"].MatchValue = _MatchValue


os.environ.setdefault("HOLON_TENANT_ID", "acme")
os.environ.setdefault("HOLON_WORKSPACE_ID", "main")

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "libs"))
sys.path.insert(0, str(ROOT / "services" / "intelligence"))

from app.vector_store import (  # noqa: E402
    COLLECTION_NAME,
    metadata_point_id,
    semantic_search,
)


def test_metadata_point_id_includes_tenant() -> None:
    a = metadata_point_id(tenant_id="acme", source="glossary", urn="encours")
    b = metadata_point_id(tenant_id="other", source="glossary", urn="encours")
    assert a != b
    assert a == metadata_point_id(tenant_id="acme", source="glossary", urn="encours")


class _FakeEmbedder:
    dimension = 3

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class _FakeQdrant:
    def __init__(self) -> None:
        self.last_filter = None

    async def query_points(self, *, collection_name, query, query_filter=None, limit=5):
        self.last_filter = query_filter
        assert collection_name == COLLECTION_NAME
        point = SimpleNamespace(
            score=0.9,
            payload={
                "text": "encours: lifetime value",
                "source": "glossary",
                "urn": "encours",
                "tenant_id": "acme",
            },
        )
        return SimpleNamespace(points=[point])


def test_semantic_search_passes_tenant_filter() -> None:
    client = _FakeQdrant()
    hits = asyncio.run(
        semantic_search(client, _FakeEmbedder(), query_text="encours", tenant_id="acme", limit=3)
    )
    assert len(hits) == 1
    assert hits[0]["tenant_id"] == "acme"
    assert client.last_filter is not None
    must = client.last_filter.must
    assert len(must) == 1
    assert must[0].key == "tenant_id"
    assert must[0].match.value == "acme"
