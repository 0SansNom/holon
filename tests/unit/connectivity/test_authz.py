"""Unit tests for Connectivity ReBAC helpers."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

# Connectivity deps reads env at import time.
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

from holon_common import HolonError  # noqa: E402
from holon_common.spicedb_id import spicedb_object_id  # noqa: E402

from app import authz_seed, deps  # noqa: E402
from app.deps import pipeline_urn, source_urn, workspace_urn  # noqa: E402
from app.routers import sources  # noqa: E402


def test_source_urn_includes_workspace_segment() -> None:
    assert source_urn("acme", "main", "reviews") == "hl:acme:main:source:reviews"


def test_pipeline_urn_uses_workspace_not_global() -> None:
    assert pipeline_urn("acme", "main", "enrich") == "hl:acme:main:pipeline:enrich"
    assert ":global:pipeline:" not in pipeline_urn("acme", "main", "enrich")


def test_workspace_urn_shape() -> None:
    assert workspace_urn("acme", "main") == "hl:acme:global:workspace:main"


class _Principal:
    tenant_id = "acme"


class _Registry:
    def __init__(self, source):
        self.source = source

    async def get_source(self, pool, tenant_id, name):
        return self.source


def _failing_seed(monkeypatch) -> None:
    async def _fail(*args, **kwargs):
        raise RuntimeError("spicedb down")

    monkeypatch.setattr(authz_seed, "seed_source_parent_workspace", _fail)


def test_seed_failure_compensates_new_row(monkeypatch) -> None:
    _failing_seed(monkeypatch)
    deleted = []

    async def _compensate():
        deleted.append(True)

    with pytest.raises(HolonError) as exc:
        asyncio.run(
            deps._seed_source_authz(
                tenant_id="acme", workspace_id="main", name="s", compensate_delete=_compensate
            )
        )
    assert exc.value.error_name == "AuthzSeedFailed"
    assert deleted == [True]


def test_seed_failure_keeps_preexisting_row(monkeypatch) -> None:
    _failing_seed(monkeypatch)
    with pytest.raises(HolonError):
        asyncio.run(
            deps._seed_source_authz(
                tenant_id="acme", workspace_id="main", name="s", compensate_delete=None
            )
        )


def test_source_update_requires_write_on_existing_source(monkeypatch) -> None:
    checked = []

    async def _authorize(principal, permission, *, name, workspace_id=None):
        checked.append((permission, name, workspace_id))

    monkeypatch.setattr(sources, "_authorize_source", _authorize)
    existing = asyncio.run(
        sources._authorize_source_update(
            _Registry({"name": "s", "workspace_id": "main"}), _Principal(), "s", "main"
        )
    )
    assert existing is not None
    assert checked == [("write", "s", "main")]


def test_source_update_rejects_workspace_move(monkeypatch) -> None:
    async def _authorize(*args, **kwargs):
        return None

    monkeypatch.setattr(sources, "_authorize_source", _authorize)
    with pytest.raises(HolonError) as exc:
        asyncio.run(
            sources._authorize_source_update(
                _Registry({"name": "s", "workspace_id": "main"}), _Principal(), "s", "other"
            )
        )
    assert exc.value.error_name == "SourceWorkspaceMismatch"


def test_new_source_skips_source_authz(monkeypatch) -> None:
    async def _authorize(*args, **kwargs):
        raise AssertionError("no source row to authorize against")

    monkeypatch.setattr(sources, "_authorize_source", _authorize)
    assert asyncio.run(
        sources._authorize_source_update(_Registry(None), _Principal(), "s", "main")
    ) is None


class _FakeAuthz:
    def __init__(self, readable=None, mandant_readable=None, lookup_fails=False):
        self.readable = readable or {}
        self.mandant_readable = mandant_readable
        self.lookup_fails = lookup_fails
        self.written: list[str] = []
        self.deleted: list[str] = []

    async def lookup_resource_ids(self, *, resource_type, permission, principal_urn):
        if self.lookup_fails:
            raise RuntimeError("lookup down")
        table = self.mandant_readable if principal_urn == "mandant" else self.readable
        return {spicedb_object_id(urn) for urn in table}

    async def check_rebac(self, principal_urn, resource_type, resource_urn, permission):
        table = self.mandant_readable if principal_urn == "mandant" else self.readable
        return resource_urn in table

    async def write_relationship(self, *, resource_urn, **kwargs):
        self.written.append(resource_urn)

    async def delete_relationship(self, *, resource_urn, **kwargs):
        self.deleted.append(resource_urn)


class _ListPrincipal:
    tenant_id = "acme"
    urn = "user"

    def __init__(self, on_behalf_of=None):
        self.on_behalf_of = on_behalf_of


_ROWS = [
    {"name": "a", "workspace_id": "main"},
    {"name": "b", "workspace_id": "other"},
    {"name": "c", "workspace_id": None},
]


@pytest.mark.parametrize("lookup_fails", [False, True])
def test_filter_readable_keeps_only_granted_rows(monkeypatch, lookup_fails) -> None:
    fake = _FakeAuthz(
        readable={source_urn("acme", "main", "a"), source_urn("acme", "main", "c")},
        lookup_fails=lookup_fails,
    )
    monkeypatch.setattr(deps, "authz", fake)
    kept = asyncio.run(deps._filter_readable(_ListPrincipal(), "source", _ROWS))
    assert [row["name"] for row in kept] == ["a", "c"]


def test_filter_readable_intersects_mandant(monkeypatch) -> None:
    fake = _FakeAuthz(
        readable={source_urn("acme", "main", "a"), source_urn("acme", "other", "b")},
        mandant_readable={source_urn("acme", "other", "b")},
    )
    monkeypatch.setattr(deps, "authz", fake)
    kept = asyncio.run(deps._filter_readable(_ListPrincipal("mandant"), "source", _ROWS))
    assert [row["name"] for row in kept] == ["b"]


def test_unlink_uses_stored_workspace(monkeypatch) -> None:
    fake = _FakeAuthz()
    monkeypatch.setattr(deps, "authz", fake)
    asyncio.run(deps._unlink_resource_authz("pipeline", tenant_id="acme", workspace_id="other", name="p"))
    assert fake.deleted == [pipeline_urn("acme", "other", "p")]


class _FakeConn:
    def __init__(self, tables):
        self.tables = tables

    async def fetch(self, sql):
        table = sql.rsplit("FROM ", 1)[1].strip()
        return self.tables.get(table, [])


class _FakePool:
    def __init__(self, tables, done=False):
        self.tables = tables
        self.done = done

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return _FakeConn(pool.tables)

            async def __aexit__(self, *exc):
                return False

        return _Ctx()

    async def fetchval(self, sql, key):
        return 1 if self.done else None

    async def execute(self, sql, key):
        self.done = True


_TABLES = {
    "generic_rest_source": [{"tenant_id": "acme", "name": "r", "workspace_id": None}],
    "pipeline_definition": [{"tenant_id": "acme", "name": "p", "workspace_id": "other"}],
}


def test_backfill_runs_once() -> None:
    fake = _FakeAuthz()
    pool = _FakePool(_TABLES)
    asyncio.run(authz_seed.ensure_authz_seeded(fake, pool))
    assert fake.written == [source_urn("acme", "main", "r"), pipeline_urn("acme", "other", "p")]
    assert pool.done

    fake.written.clear()
    asyncio.run(authz_seed.ensure_authz_seeded(fake, pool))
    assert fake.written == []


def test_backfill_not_marked_done_on_failure(monkeypatch) -> None:
    async def _fail(*args, **kwargs):
        raise RuntimeError("spicedb down")

    monkeypatch.setattr(authz_seed, "seed_pipeline_parent_workspace", _fail)
    pool = _FakePool(_TABLES)
    asyncio.run(authz_seed.ensure_authz_seeded(_FakeAuthz(), pool))
    assert not pool.done
