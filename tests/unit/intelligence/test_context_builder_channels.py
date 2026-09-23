"""Unit tests for lexical Knowledge /search channel in context_builder."""

from __future__ import annotations

import asyncio
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

# Stub heavy optional deps before importing context_builder on host.
for _name, _attrs in (
    ("qdrant_client", {"AsyncQdrantClient": MagicMock}),
    (
        "qdrant_client.models",
        {
            "Distance": object,
            "FieldCondition": MagicMock,
            "Filter": MagicMock,
            "MatchValue": MagicMock,
            "PointStruct": MagicMock,
            "VectorParams": MagicMock,
        },
    ),
    ("voyageai", {"AsyncClient": MagicMock}),
    ("sentence_transformers", {"SentenceTransformer": MagicMock}),
    ("anthropic", {"AsyncAnthropic": MagicMock}),
):
    if _name not in sys.modules:
        mod = types.ModuleType(_name)
        for k, v in _attrs.items():
            setattr(mod, k, v)
        sys.modules[_name] = mod

os.environ.setdefault("HOLON_TENANT_ID", "acme")
os.environ.setdefault("HOLON_WORKSPACE_ID", "main")

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "libs"))
sys.path.insert(0, str(ROOT / "services" / "intelligence"))

from app import context_builder  # noqa: E402
from app.context_builder import build_context, _parse_search_hit  # noqa: E402


def test_parse_search_hit_splits_knowledge_urn() -> None:
    object_type, instance_id, summary = _parse_search_hit(
        {"urn": "Customer:acme:7", "object_type": "Customer", "text": "Acme Corp"}
    )
    assert object_type == "Customer"
    assert instance_id == "7"
    assert "Customer/7" in summary


def test_build_context_uses_lexical_before_semantic(monkeypatch) -> None:
    lexical_hits = [{"urn": "Customer:acme:2", "object_type": "Customer", "text": "Jane"}]

    async def _fake_lexical(*args, **kwargs):
        return lexical_hits

    async def _fail_semantic(*args, **kwargs):
        raise AssertionError("semantic_search must not run when lexical hits exist")

    async def _no_hydrate(*args, **kwargs):
        return None

    monkeypatch.setattr(context_builder, "_lexical_search", _fake_lexical)
    monkeypatch.setattr(context_builder, "semantic_search", _fail_semantic)
    monkeypatch.setattr(context_builder, "_structural_lookup", _no_hydrate)

    result = asyncio.run(
        build_context(
            query_text="find customers named Jane",
            authorization="Bearer x",
            knowledge_url="http://knowledge",
            qdrant=MagicMock(),
            embedder=MagicMock(),
            glossary_terms=[],
            tenant_id="acme",
        )
    )
    assert any(item.channel == "lexical" for item in result.items)
    assert result.items[0].urn == "Customer:acme:2"


def test_build_context_falls_back_to_semantic_when_lexical_empty(monkeypatch) -> None:
    async def _empty_lexical(*args, **kwargs):
        return []

    async def _semantic(*args, **kwargs):
        assert kwargs.get("tenant_id") == "acme"
        return [{"text": "encours: LTV", "source": "glossary", "urn": "encours", "tenant_id": "acme"}]

    monkeypatch.setattr(context_builder, "_lexical_search", _empty_lexical)
    monkeypatch.setattr(context_builder, "semantic_search", _semantic)

    result = asyncio.run(
        build_context(
            query_text="What does encours mean in this system?",
            authorization="Bearer x",
            knowledge_url="http://knowledge",
            qdrant=MagicMock(),
            embedder=MagicMock(),
            glossary_terms=[],
            tenant_id="acme",
        )
    )
    assert [item.channel for item in result.items] == ["semantic"]
    assert result.items[0].urn == "glossary:encours"


def test_semantic_isolation_passes_caller_tenant(monkeypatch) -> None:
    seen: dict = {}

    async def _empty_lexical(*args, **kwargs):
        return []

    async def _semantic(*args, **kwargs):
        seen["tenant_id"] = kwargs["tenant_id"]
        return []

    monkeypatch.setattr(context_builder, "_lexical_search", _empty_lexical)
    monkeypatch.setattr(context_builder, "semantic_search", _semantic)

    asyncio.run(
        build_context(
            query_text="anything",
            authorization="Bearer x",
            knowledge_url="http://knowledge",
            qdrant=MagicMock(),
            embedder=MagicMock(),
            glossary_terms=[],
            tenant_id="other-tenant",
        )
    )
    assert seen["tenant_id"] == "other-tenant"
