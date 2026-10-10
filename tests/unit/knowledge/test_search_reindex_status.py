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

    monkeypatch.setattr(catalog.catalog_reindex, "reindex_object_type_search", reindex)

    assert asyncio.run(catalog.reindex_search_from_serving_store(_Pool(), "http://os", "pw")) == 0
    assert catalog.search_reindex_status["state"] == "ok"
    assert catalog.search_reindex_status["pending_outside_policy"] == 0


def test_failed_type_is_retried_and_reported_until_it_succeeds(catalog, monkeypatch) -> None:
    _patch_count(catalog, monkeypatch, [5, 0])
    calls: list[str] = []
    seen_while_degraded: list[dict] = []
    failed_gauge: list[float] = []

    async def reindex(pool, *, object_type_name, **kwargs):
        calls.append(object_type_name)
        if object_type_name == "Invoice" and calls.count("Invoice") == 1:
            raise RuntimeError("opensearch down")
        return {}

    async def sleep(delay):
        seen_while_degraded.append(dict(catalog.search_reindex_status))
        failed_gauge.append(catalog.SEARCH_REINDEX_FAILED_OBJECT_TYPES._value.get())

    monkeypatch.setattr(catalog.catalog_reindex, "reindex_object_type_search", reindex)
    monkeypatch.setattr(catalog.catalog_reindex.asyncio, "sleep", sleep)

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
    assert failed_gauge == [1]
    assert catalog.SEARCH_REINDEX_FAILED_OBJECT_TYPES._value.get() == 0
    assert catalog.SEARCH_DOCUMENTS_OUTSIDE_POLICY._value.get() == 0


def test_type_deleted_during_reindex_is_not_retried(catalog, monkeypatch) -> None:
    _patch_count(catalog, monkeypatch, [5, 0])

    async def reindex(pool, *, object_type_name, **kwargs):
        if object_type_name == "Invoice":
            raise ValueError("unknown ObjectType: 'Invoice'")
        return {}

    async def sleep(delay):
        raise AssertionError("should not retry a deleted type")

    monkeypatch.setattr(catalog.catalog_reindex, "reindex_object_type_search", reindex)
    monkeypatch.setattr(catalog.catalog_reindex.asyncio, "sleep", sleep)

    assert asyncio.run(catalog.reindex_search_from_serving_store(_Pool(), "http://os", "pw")) == 1
    assert catalog.search_reindex_status["state"] == "ok"


def test_unreachable_catalog_reports_error(catalog, monkeypatch) -> None:
    _patch_count(catalog, monkeypatch, [RuntimeError("opensearch down")])

    class _BrokenPool:
        async def fetch(self, sql: str):
            raise RuntimeError("postgres down")

    assert asyncio.run(catalog.reindex_search_from_serving_store(_BrokenPool(), "http://os", "pw")) == 0
    assert catalog.search_reindex_status == {"state": "error"}
    assert catalog.SEARCH_DOCUMENTS_OUTSIDE_POLICY._value.get() == -1


def _skipped_gauge(catalog, object_type: str) -> float:
    return catalog.SEARCH_ROWS_SKIPPED_INVALID.labels(tenant="acme", object_type=object_type)._value.get()


class _Conn:
    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _IngestPool:
    def acquire(self):
        return _Conn()


def _patch_ingest(catalog, monkeypatch, invalid_ids: set[str]) -> list[dict]:
    rows = [{"id": "1"}, {"id": "2"}, {"id": "3"}]
    indexed: list[dict] = []

    async def fetch_rows(dataset_name, tenant_id, iceberg_config):
        return rows

    async def noop(*args, **kwargs):
        return None

    async def get_object_type_by_dataset(pool, tenant_id, dataset_urn):
        return {"name": "Customer", "urn": "hl:acme:main:object-type:Customer", "property_mapping": {}}

    async def get_object_type(pool, urn):
        return {"property_types": {"email": "email"}, "classification": "INTERNAL"}

    async def partition(pool, tenant_id, *, property_mapping, property_types, rows):
        valid = [row for row in rows if row["id"] not in invalid_ids]
        return valid, [row for row in rows if row["id"] in invalid_ids]

    async def empty(*args, **kwargs):
        return []

    async def no_markings(*args, **kwargs):
        return {}

    async def index_rows(url, password, *, rows, **kwargs):
        indexed.extend(rows)

    monkeypatch.setattr(catalog, "_fetch_dataset_rows", fetch_rows)
    monkeypatch.setattr(catalog.serving_store, "materialize", noop)
    monkeypatch.setattr(catalog.ontology, "get_object_type_by_dataset", get_object_type_by_dataset)
    monkeypatch.setattr(catalog.ontology, "get_object_type", get_object_type)
    monkeypatch.setattr(catalog.ontology, "partition_rows_by_property_types", partition)
    monkeypatch.setattr(catalog.ontology, "list_shared_property_types", empty)
    monkeypatch.setattr(catalog.ontology, "get_instance_markings_bulk", no_markings)
    monkeypatch.setattr(catalog.ontology, "get_property_classifications", no_markings)
    monkeypatch.setattr(catalog.search, "index_rows", index_rows)
    return indexed


def _ingest(catalog) -> None:
    payload = {"dataset_name": "customers", "dataset_urn": "hl:acme:main:dataset:customers", "snapshot_id": 7}
    asyncio.run(catalog._materialize_sync(_IngestPool(), "acme", "main", payload, {}, "http://os", "pw"))


def test_ingest_reports_rows_left_out_of_search_then_clears_them(catalog, monkeypatch) -> None:
    indexed = _patch_ingest(catalog, monkeypatch, invalid_ids={"2"})
    _ingest(catalog)

    assert [row["id"] for row in indexed] == ["1", "3"]
    assert catalog.search_skipped_invalid == {"hl:acme:main:object-type:Customer": 1}
    assert _skipped_gauge(catalog, "Customer") == 1

    _patch_ingest(catalog, monkeypatch, invalid_ids=set())
    _ingest(catalog)

    assert catalog.search_skipped_invalid == {}
    assert _skipped_gauge(catalog, "Customer") == 0
