"""A scheduled sync gives up on a source that does not finish fetching in time, and the failure is recorded."""

from __future__ import annotations

import asyncio
import os
import sys
import types
from pathlib import Path

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
sys.path.insert(0, str(ROOT / "libs"))
sys.path.insert(0, str(ROOT / "services" / "connectivity"))

from holon_common import EventActor, HolonError  # noqa: E402

from app import ingest_sync  # noqa: E402

pytestmark = pytest.mark.unit

_AUDITS: list[dict] = []

_ACTOR = EventActor(type="service_account", urn="hl:acme:global:service-account:test", on_behalf_of=None)


class _FailurePool:
    def __init__(self) -> None:
        self.failures: list[tuple] = []

    async def execute(self, sql: str, *args):
        assert "INSERT INTO sync_failure" in sql
        self.failures.append(args)


def _source(monkeypatch, fetch) -> list:
    writes: list = []
    monkeypatch.setattr(ingest_sync.deps, "pool", _FailurePool())
    monkeypatch.setattr(ingest_sync, "emit_audit", lambda **kwargs: _AUDITS.append(kwargs))

    async def no_plugin(pool, dataset_name, tenant_id):
        return None

    async def resolve(pool, tenant_id, dataset_name):
        kind = types.SimpleNamespace(
            fetch_for_dataset=fetch,
            connector_local_name=lambda dataset: f"sql-{dataset}",
            uses_append=lambda row: False,
        )
        return kind, {"status": "active"}

    monkeypatch.setattr(ingest_sync.plugin_registry, "load_active_plugin_for_dataset", no_plugin)
    monkeypatch.setattr(ingest_sync.source_kinds, "resolve_registered_source", resolve)
    monkeypatch.setattr(ingest_sync.iceberg_writer, "write_snapshot", lambda *args, **kwargs: writes.append(args))
    return writes


def test_a_source_slower_than_the_deadline_fails_without_writing(monkeypatch) -> None:
    async def hang(pool, tenant_id, dataset_name):
        await asyncio.sleep(60)

    writes = _source(monkeypatch, hang)

    with pytest.raises(HolonError) as caught:
        asyncio.run(
            ingest_sync._run_sync_for_dataset("orders", actor=_ACTOR, tenant_id="acme", fetch_timeout=0.01)
        )

    assert caught.value.error_name == "SourceFetchTimeout"
    assert caught.value.status_code == 503
    assert writes == []
    ((tenant_id, connector_urn, dataset_urn, error_name, error, timed_out, started_at, finished_at),) = (
        ingest_sync.deps.pool.failures
    )
    assert (tenant_id, connector_urn, dataset_urn) == (
        "acme",
        "hl:acme:global:connector:sql-orders",
        "hl:acme:main:dataset:orders",
    )
    assert (error_name, timed_out) == ("SourceFetchTimeout", True)
    assert started_at <= finished_at
    assert _AUDITS[-1]["action"] == "connectivity.sync.failed"
    assert _AUDITS[-1]["outcome"] == "failure"


def test_a_timeout_raised_by_the_source_itself_is_not_relabelled(monkeypatch) -> None:
    async def driver_timeout(pool, tenant_id, dataset_name):
        raise TimeoutError("driver socket timeout")

    _source(monkeypatch, driver_timeout)

    with pytest.raises(TimeoutError, match="driver socket timeout"):
        asyncio.run(
            ingest_sync._run_sync_for_dataset("orders", actor=_ACTOR, tenant_id="acme", fetch_timeout=30)
        )


def test_a_source_error_is_recorded_as_a_failed_run(monkeypatch) -> None:
    async def broken(pool, tenant_id, dataset_name):
        raise ingest_sync.source_registry_base.SourceFetchError("relation \"orders\" does not exist")

    _source(monkeypatch, broken)

    with pytest.raises(HolonError):
        asyncio.run(ingest_sync._run_sync_for_dataset("orders", actor=_ACTOR, tenant_id="acme"))

    ((_, _, _, error_name, error, timed_out, _, _),) = ingest_sync.deps.pool.failures
    assert (error_name, timed_out) == ("DatasetValidationFailed", False)
    assert "does not exist" in error


def test_an_unknown_dataset_is_not_recorded(monkeypatch) -> None:
    _source(monkeypatch, None)

    async def unknown(pool, tenant_id, dataset_name):
        return None

    monkeypatch.setattr(ingest_sync.source_kinds, "resolve_registered_source", unknown)

    with pytest.raises(HolonError):
        asyncio.run(ingest_sync._run_sync_for_dataset("typo", actor=_ACTOR, tenant_id="acme"))

    assert ingest_sync.deps.pool.failures == []
