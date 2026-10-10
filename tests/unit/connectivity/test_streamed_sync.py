"""A sync streams source rows in batches and makes them visible with one Iceberg commit."""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("HOLON_TENANT_ID", "acme")
os.environ.setdefault("HOLON_WORKSPACE_ID", "main")
os.environ.setdefault("HOLON_JWT_SECRET", "unit-test-secret")
os.environ.setdefault("HOLON_DB_URL", "postgresql://holon:holon@localhost:5432/holon_connectivity")
os.environ.setdefault("HOLON_KNOWLEDGE_URL", "http://localhost:8003")
os.environ.setdefault("HOLON_KAFKA_BOOTSTRAP", "localhost:9092")
os.environ.setdefault("HOLON_SPICEDB_URL", "http://localhost:8443")
os.environ.setdefault("HOLON_SPICEDB_PRESHARED_KEY", "change-me")
os.environ.setdefault("HOLON_OPA_URL", "http://localhost:8181")
os.environ.setdefault("HOLON_ICEBERG_CATALOG_URI", "http://localhost:8181")
os.environ.setdefault("HOLON_ICEBERG_WAREHOUSE", "s3://holon-warehouse/")
os.environ.setdefault("HOLON_S3_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "holon")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "holon")
os.environ.setdefault("AWS_REGION", "us-east-1")

ROOT = Path(__file__).resolve().parents[3]
_STUBBED_ELSEWHERE = ("app", "holon_common", "pyarrow", "pyiceberg", "asyncpg", "prometheus_client")

pytestmark = pytest.mark.unit

_CONFIG = dict(catalog_uri="x", warehouse="x", s3_endpoint="x", access_key="x", secret_key="x", region="x")
iceberg_writer = ingest_sync = sql_drivers = sql_source_fetch = None
HolonError = _ACTOR = None


def _is_stubbable(name: str) -> bool:
    return name.split(".")[0] in _STUBBED_ELSEWHERE


@pytest.fixture(autouse=True)
def real_modules():
    """Import the real modules; other unit tests stub `app.*` and pyarrow in sys.modules."""
    global iceberg_writer, ingest_sync, sql_drivers, sql_source_fetch, HolonError, _ACTOR
    saved = {name: module for name, module in sys.modules.items() if _is_stubbable(name)}
    for name in saved:
        del sys.modules[name]
    try:
        with patch.object(sys, "path", [str(ROOT / "services" / "connectivity"), str(ROOT / "libs"), *sys.path]):
            holon_common = importlib.import_module("holon_common")
            iceberg_writer = importlib.import_module("app.iceberg_writer")
            ingest_sync = importlib.import_module("app.ingest_sync")
            sql_drivers = importlib.import_module("app.sql_drivers")
            sql_source_fetch = importlib.import_module("app.sql_source_fetch")
            HolonError = holon_common.HolonError
            _ACTOR = holon_common.EventActor(
                type="service_account", urn="hl:acme:global:service-account:test", on_behalf_of=None
            )
            yield
    finally:
        for name in [name for name in sys.modules if _is_stubbable(name)]:
            del sys.modules[name]
        sys.modules.update(saved)


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    from pyiceberg.catalog.sql import SqlCatalog

    sql_catalog = SqlCatalog("test", uri=f"sqlite:///{tmp_path}/catalog.db", warehouse=f"file://{tmp_path}")
    monkeypatch.setattr(iceberg_writer, "_load_catalog", lambda *args: sql_catalog)
    return sql_catalog


def _rows(catalog) -> list[dict]:
    table = catalog.load_table(("raw", "acme__orders"))
    return sorted(table.scan().to_arrow().to_pylist(), key=lambda row: row["id"])


def test_batches_land_in_one_commit_and_late_columns_are_kept(catalog) -> None:
    writer = iceberg_writer.SnapshotWriter("orders", tenant_id="acme", mode="overwrite", **_CONFIG)
    writer.add([{"id": 1, "note": None}, {"id": 2, "note": None}])
    writer.add([{"id": 3, "note": "late", "amount": 1.5}])

    assert catalog.load_table(("raw", "acme__orders")).current_snapshot() is None

    result = writer.commit()

    assert result.row_count == 3
    assert _rows(catalog) == [
        {"id": 1, "note": None, "amount": None},
        {"id": 2, "note": None, "amount": None},
        {"id": 3, "note": "late", "amount": 1.5},
    ]


def test_overwrite_replaces_and_append_adds(catalog) -> None:
    first = iceberg_writer.SnapshotWriter("orders", tenant_id="acme", mode="overwrite", **_CONFIG)
    first.add([{"id": 1}, {"id": 2}])
    first.commit()

    again = iceberg_writer.SnapshotWriter("orders", tenant_id="acme", mode="overwrite", **_CONFIG)
    again.add([{"id": 9}])
    again.commit()
    assert _rows(catalog) == [{"id": 9}]

    more = iceberg_writer.SnapshotWriter("orders", tenant_id="acme", mode="append", **_CONFIG)
    more.add([{"id": 10}])
    more.add([{"id": 11}])
    assert more.commit().row_count == 3
    assert [row["id"] for row in _rows(catalog)] == [9, 10, 11]


def test_an_ambiguous_commit_that_landed_is_not_retried(catalog, monkeypatch) -> None:
    writer = iceberg_writer.SnapshotWriter("orders", tenant_id="acme", mode="append", **_CONFIG)
    writer.add([{"id": 1}])
    real_commit = writer._txn.commit_transaction

    def commit_then_lose_the_answer():
        real_commit()
        raise iceberg_writer.CommitStateUnknownException("gateway timeout")

    monkeypatch.setattr(writer._txn, "commit_transaction", commit_then_lose_the_answer)
    monkeypatch.setattr(iceberg_writer.time, "sleep", lambda seconds: None)

    assert writer.commit().row_count == 1
    assert _rows(catalog) == [{"id": 1}]


def test_row_batches_regroup_any_source_shape() -> None:
    async def pages():
        for page in ([{"n": 1}, {"n": 2}, {"n": 3}], [{"n": 4}], [{"n": 5}, {"n": 6}]):
            yield page

    async def collect(source):
        return [[row["n"] for row in batch] async for batch in ingest_sync._row_batches(source, 4)]

    assert asyncio.run(collect(pages())) == [[1, 2, 3, 4], [5, 6]]
    assert asyncio.run(collect([{"n": index} for index in range(5)])) == [[0, 1, 2, 3], [4]]


def _sync_source(monkeypatch, fetch) -> None:
    async def no_plugin(pool, dataset_name, tenant_id):
        return None

    async def resolve(pool, tenant_id, dataset_name):
        kind = types.SimpleNamespace(
            fetch_for_dataset=fetch,
            connector_local_name=lambda dataset: f"sql-{dataset}",
            uses_append=lambda row: False,
        )
        return kind, {"status": "active"}

    async def finalize(**kwargs):
        return kwargs["result"]

    monkeypatch.setattr(ingest_sync.plugin_registry, "load_active_plugin_for_dataset", no_plugin)
    monkeypatch.setattr(ingest_sync.source_kinds, "resolve_registered_source", resolve)
    monkeypatch.setattr(ingest_sync, "_finalize_sync", finalize)
    monkeypatch.setattr(ingest_sync, "ICEBERG_CONFIG", _CONFIG)
    monkeypatch.setattr(ingest_sync, "INGEST_BATCH_ROWS", 2)


def test_sync_streams_batches_then_commits_the_cursor(catalog, monkeypatch) -> None:
    committed: list[str] = []
    written_before_cursor: list[int] = []

    async def fetch(pool, tenant_id, dataset_name):
        async def batches():
            yield [{"id": 1}, {"id": 2}, {"id": 3}]
            yield [{"id": 4}]

        async def commit():
            written_before_cursor.append(len(_rows(catalog)))
            committed.append("cursor")

        return batches(), commit

    _sync_source(monkeypatch, fetch)

    result = asyncio.run(ingest_sync._run_sync_for_dataset("orders", actor=_ACTOR, tenant_id="acme"))

    assert result.row_count == 4
    assert committed == ["cursor"]
    assert written_before_cursor == [4]
    table = catalog.load_table(("raw", "acme__orders"))
    assert len([s for s in table.snapshots() if s.summary.get("holon.sync-id")]) == 2


def test_a_source_failing_mid_stream_leaves_the_table_and_cursor_untouched(catalog, monkeypatch) -> None:
    committed: list[str] = []
    previous = iceberg_writer.SnapshotWriter("orders", tenant_id="acme", mode="overwrite", **_CONFIG)
    previous.add([{"id": 100}])
    previous.commit()

    async def fetch(pool, tenant_id, dataset_name):
        async def batches():
            yield [{"id": 1}, {"id": 2}, {"id": 3}]
            raise sql_source_fetch.SourceFetchError("connection reset")

        async def commit():
            committed.append("cursor")

        return batches(), commit

    _sync_source(monkeypatch, fetch)

    with pytest.raises(HolonError) as caught:
        asyncio.run(ingest_sync._run_sync_for_dataset("orders", actor=_ACTOR, tenant_id="acme"))

    assert caught.value.error_name == "DatasetValidationFailed"
    assert committed == []
    assert _rows(catalog) == [{"id": 100}]


class _SourcePool:
    def __init__(self, *, table_name, query, cursor_property, last_cursor_value, boundary_keys=None) -> None:
        self.source = {
            "connection_name": "warehouse",
            "table_name": table_name,
            "query": query,
            "cursor_property": cursor_property,
            "last_cursor_value": last_cursor_value,
            "cursor_boundary_keys": boundary_keys,
        }
        self.updates: list[tuple] = []

    async def fetchrow(self, sql: str, *args):
        if "FROM sql_source" in sql:
            return self.source
        return {
            "dialect": "postgres",
            "host": "db.example.com",
            "port": 5432,
            "database": "sales",
            "warehouse": None,
            "username": "reader",
            "password": None,
            "secret_ref": "env:HOLON_CONN_ACME__WAREHOUSE",
            "use_tls": False,
        }

    async def execute(self, sql: str, *args):
        self.updates.append((sql, args))


def _stream(monkeypatch, batches: list[list[dict]]) -> list[dict]:
    calls: list[dict] = []

    async def stream_dicts(**kwargs):
        calls.append(kwargs)
        for batch in batches:
            yield batch

    monkeypatch.setattr(sql_source_fetch, "pin_connector_host", lambda host: host)
    monkeypatch.setattr(sql_source_fetch, "resolve_source_secret", lambda ref, tenant_id: "s3cret")
    monkeypatch.setattr(sql_source_fetch, "connector_secret", lambda **kwargs: "s3cret")
    monkeypatch.setattr(sql_drivers, "stream_dicts", stream_dicts)
    return calls


async def _drain(pool):
    batches, commit = await sql_source_fetch.fetch_for_dataset(pool, "acme", "orders", batch_size=2)
    rows = [row async for batch in batches for row in batch]
    await commit()
    return rows


def test_incremental_table_cursor_spans_batches(monkeypatch) -> None:
    calls = _stream(monkeypatch, [[{"id": 1, "ts": 5}, {"id": 2, "ts": 9}], [{"id": 3, "ts": 7}]])
    pool = _SourcePool(table_name="orders", query=None, cursor_property="ts", last_cursor_value="4")

    rows = asyncio.run(_drain(pool))

    assert [row["id"] for row in rows] == [1, 2, 3]
    assert calls[0]["batch_size"] == 2
    ((sql, args),) = pool.updates
    assert "last_cursor_value" in sql and args[0] == "9"


def test_incremental_table_skips_rows_already_stored_at_the_boundary(monkeypatch) -> None:
    stored = {"id": 2, "ts": 9}
    first = _SourcePool(table_name="orders", query=None, cursor_property="ts", last_cursor_value=None)
    _stream(monkeypatch, [[{"id": 1, "ts": 5}], [stored]])
    asyncio.run(_drain(first))
    boundary = first.updates[0][1][1]

    _stream(monkeypatch, [[stored], [{"id": 4, "ts": 9}]])
    resumed = _SourcePool(
        table_name="orders", query=None, cursor_property="ts", last_cursor_value="9", boundary_keys=boundary
    )

    assert [row["id"] for row in asyncio.run(_drain(resumed))] == [4]


def test_query_cursor_keeps_the_max_across_batches(monkeypatch) -> None:
    _stream(monkeypatch, [[{"ts": 3}, {"ts": 8}], [{"ts": 6}]])
    pool = _SourcePool(table_name=None, query="SELECT * FROM orders", cursor_property="ts", last_cursor_value="1")

    asyncio.run(_drain(pool))

    ((sql, args),) = pool.updates
    assert args[0] == "8"


def test_unchanged_cursor_is_not_rewritten(monkeypatch) -> None:
    _stream(monkeypatch, [])
    pool = _SourcePool(table_name="orders", query=None, cursor_property="ts", last_cursor_value="4")

    assert asyncio.run(_drain(pool)) == []
    assert pool.updates == []


def test_driver_errors_mid_stream_become_source_fetch_errors(monkeypatch) -> None:
    async def stream_dicts(**kwargs):
        yield [{"id": 1}]
        raise OSError("connection reset by peer")

    _stream(monkeypatch, [])
    monkeypatch.setattr(sql_drivers, "stream_dicts", stream_dicts)
    pool = _SourcePool(table_name="orders", query=None, cursor_property=None, last_cursor_value=None)

    with pytest.raises(sql_source_fetch.SourceFetchError, match="connection reset"):
        asyncio.run(_drain(pool))
