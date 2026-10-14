"""Search reindex reads the serving store one page at a time."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_STUBBED_ELSEWHERE = ("app", "holon_common", "pyiceberg", "asyncpg", "httpx", "prometheus_client")


def _is_stubbable(name: str) -> bool:
    return name.split(".")[0] in _STUBBED_ELSEWHERE


@pytest.fixture
def reindex_module():
    saved = {name: module for name, module in sys.modules.items() if _is_stubbable(name)}
    for name in saved:
        del sys.modules[name]
    sys.modules.setdefault("duckdb", types.ModuleType("duckdb"))
    try:
        sys.path.insert(0, str(REPO_ROOT / "libs"))
        sys.path.insert(0, str(REPO_ROOT / "services" / "knowledge"))
        yield importlib.import_module("app.catalog_reindex")
    finally:
        for name in [name for name in sys.modules if _is_stubbable(name)]:
            del sys.modules[name]
        sys.modules.update(saved)


def test_reindex_indexes_each_page_then_deletes_once(reindex_module, monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}],
        [{"id": "3", "name": "c"}],
    ]

    async def list_instances(pool, object_type, tenant_id, **kwargs):
        assert kwargs["limit"] == reindex_module._REINDEX_PAGE
        return pages.pop(0) if pages else []

    indexed: list[list] = []

    async def index_rows(*args, **kwargs):
        indexed.append(list(kwargs["rows"]))

    deleted = {"n": 0}

    async def delete_object_type_documents(*args, **kwargs):
        deleted["n"] += 1

    monkeypatch.setattr(reindex_module.serving_store, "list_instances", list_instances)
    monkeypatch.setattr(reindex_module.search, "index_rows", index_rows)
    monkeypatch.setattr(reindex_module.search, "delete_object_type_documents", delete_object_type_documents)
    monkeypatch.setattr(
        reindex_module.ontology,
        "get_object_type",
        AsyncMock(return_value={"property_mapping": {}, "property_types": {}, "classification": "public"}),
    )
    monkeypatch.setattr(reindex_module.ontology, "list_shared_property_types", AsyncMock(return_value=[]))
    monkeypatch.setattr(reindex_module.ontology, "get_property_classifications", AsyncMock(return_value={}))
    monkeypatch.setattr(reindex_module.ontology, "get_instance_markings_bulk", AsyncMock(return_value={}))

    # Two rows then one: the page size in this test is the module constant, so
    # force a tiny page to prove the loop continues.
    monkeypatch.setattr(reindex_module, "_REINDEX_PAGE", 2)

    async def run():
        return await reindex_module.reindex_object_type_search(
            MagicMock(),
            object_type_name="Customer",
            object_type_urn="hl:acme:main:object-type:Customer",
            tenant_id="acme",
            opensearch_url="http://search",
            opensearch_password="pw",
        )

    result = asyncio.run(run())
    assert indexed == [[{"id": "1", "name": "a"}, {"id": "2", "name": "b"}], [{"id": "3", "name": "c"}]]
    assert deleted["n"] == 1
    assert result["indexed"] == 3
    assert result["materialized_total"] == 3
