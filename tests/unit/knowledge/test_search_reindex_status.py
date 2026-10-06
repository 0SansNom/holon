"""Boot search reindex retries failed types and reports progress for /ready."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_STUBBED_ELSEWHERE = ("app", "holon_common", "pyiceberg", "asyncpg", "httpx", "prometheus_client")


def _is_stubbable(name: str) -> bool:
    return name.split(".")[0] in _STUBBED_ELSEWHERE


@pytest.fixture
def catalog():
    """Import the real `app.catalog`; other unit modules stub `app.*` and its deps in sys.modules."""
    saved = {name: module for name, module in sys.modules.items() if _is_stubbable(name)}
    for name in saved:
        del sys.modules[name]
    sys.modules.setdefault("duckdb", types.ModuleType("duckdb"))
    try:
        with patch.object(sys, "path", [str(REPO_ROOT / "services" / "knowledge"), str(REPO_ROOT / "libs"), *sys.path]):
            yield importlib.import_module("app.catalog")
    finally:
        for name in [name for name in sys.modules if _is_stubbable(name)]:
            del sys.modules[name]
        sys.modules.update(saved)


_ROWS = [
    {"urn": "hl:acme:main:object-type:Customer", "name": "Customer", "tenant_id": "acme"},
    {"urn": "hl:acme:main:object-type:Invoice", "name": "Invoice", "tenant_id": "acme"},
]


class _Pool:
    async def fetch(self, sql: str):
        return _ROWS


def _patch_count(catalog, monkeypatch, counts: list) -> None:
    async def count_outside_policy(url, password):
        value = counts.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(catalog.search, "count_outside_policy", count_outside_policy)


def test_current_index_reports_ok_without_reindexing(catalog, monkeypatch) -> None:
    _patch_count(catalog, monkeypatch, [0])

    async def reindex(*args, **kwargs):
        raise AssertionError("should not reindex")

    monkeypatch.setattr(catalog, "reindex_object_type_search", reindex)

    assert asyncio.run(catalog.reindex_search_from_serving_store(_Pool(), "http://os", "pw")) == 0
    assert catalog.search_reindex_status["state"] == "ok"
    assert catalog.search_reindex_status["pending_outside_policy"] == 0


def test_failed_type_is_retried_and_reported_until_it_succeeds(catalog, monkeypatch) -> None:
    _patch_count(catalog, monkeypatch, [5, 0])
    calls: list[str] = []
    seen_while_degraded: list[dict] = []

    async def reindex(pool, *, object_type_name, **kwargs):
        calls.append(object_type_name)
        if object_type_name == "Invoice" and calls.count("Invoice") == 1:
            raise RuntimeError("opensearch down")
        return {"skipped_invalid": 2 if object_type_name == "Customer" else 0}

    async def sleep(delay):
        seen_while_degraded.append(dict(catalog.search_reindex_status))

    monkeypatch.setattr(catalog, "reindex_object_type_search", reindex)
    monkeypatch.setattr(catalog.asyncio, "sleep", sleep)

    done = asyncio.run(
        catalog.reindex_search_from_serving_store(_Pool(), "http://os", "pw", retry_seconds=(0.0,))
    )

    assert done == 2
    assert calls == ["Customer", "Invoice", "Invoice"]
    assert seen_while_degraded[0]["state"] == "degraded"
    assert seen_while_degraded[0]["failed"] == ["hl:acme:main:object-type:Invoice"]
    status = catalog.search_reindex_status
    assert status["state"] == "ok"
    assert status["failed"] == []
    assert status["attempts"] == 1
    assert status["pending_outside_policy"] == 0
    assert status["skipped_invalid"] == {"hl:acme:main:object-type:Customer": 2}


def test_type_deleted_during_reindex_is_not_retried(catalog, monkeypatch) -> None:
    _patch_count(catalog, monkeypatch, [5, 0])

    async def reindex(pool, *, object_type_name, **kwargs):
        if object_type_name == "Invoice":
            raise ValueError("unknown ObjectType: 'Invoice'")
        return {"skipped_invalid": 0}

    async def sleep(delay):
        raise AssertionError("should not retry a deleted type")

    monkeypatch.setattr(catalog, "reindex_object_type_search", reindex)
    monkeypatch.setattr(catalog.asyncio, "sleep", sleep)

    assert asyncio.run(catalog.reindex_search_from_serving_store(_Pool(), "http://os", "pw")) == 1
    assert catalog.search_reindex_status["state"] == "ok"


def test_unreachable_catalog_reports_error(catalog, monkeypatch) -> None:
    _patch_count(catalog, monkeypatch, [RuntimeError("opensearch down")])

    class _BrokenPool:
        async def fetch(self, sql: str):
            raise RuntimeError("postgres down")

    assert asyncio.run(catalog.reindex_search_from_serving_store(_BrokenPool(), "http://os", "pw")) == 0
    assert catalog.search_reindex_status == {"state": "error"}
