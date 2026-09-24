"""Unit tests for Intelligence ReBAC helpers."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

# Intelligence deps reads env at import time.
os.environ.setdefault("HOLON_TENANT_ID", "acme")
os.environ.setdefault("HOLON_WORKSPACE_ID", "main")
os.environ.setdefault("HOLON_JWT_SECRET", "unit-test-secret")
os.environ.setdefault("HOLON_DB_URL", "postgresql://holon:holon@localhost:5432/holon_intelligence")
os.environ.setdefault("HOLON_KAFKA_BOOTSTRAP", "localhost:9092")
os.environ.setdefault("HOLON_KNOWLEDGE_URL", "http://localhost:8003")
os.environ.setdefault("HOLON_QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("HOLON_SPICEDB_URL", "http://localhost:8443")
os.environ.setdefault("HOLON_SPICEDB_PRESHARED_KEY", "change-me")
os.environ.setdefault("HOLON_OPA_URL", "http://localhost:8181")
os.environ.setdefault("HOLON_S3_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "holon")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "holon")
os.environ.setdefault("AWS_REGION", "us-east-1")

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "libs"))
sys.path.insert(0, str(ROOT / "services" / "intelligence"))

from holon_common import HolonError, build_urn  # noqa: E402
from holon_common.spicedb_id import spicedb_object_id  # noqa: E402

from app import authz_seed, deps  # noqa: E402
from app.deps import ml_model_urn, tool_plugin_urn, workspace_urn  # noqa: E402


def test_tool_plugin_urn_shape() -> None:
    assert tool_plugin_urn("acme", "weather") == "hl:acme:global:tool-plugin:weather"


def test_ml_model_urn_shape() -> None:
    assert ml_model_urn("acme", "clf") == "hl:acme:global:ml-model:clf"


def test_workspace_urn_shape() -> None:
    assert workspace_urn("acme", "main") == "hl:acme:global:workspace:main"


def _failing_plugin_seed(monkeypatch) -> None:
    async def _fail(*args, **kwargs):
        raise RuntimeError("spicedb down")

    monkeypatch.setattr(authz_seed, "seed_tool_plugin_parent_workspace", _fail)


def test_seed_failure_compensates_new_row(monkeypatch) -> None:
    _failing_plugin_seed(monkeypatch)
    deleted = []

    async def _compensate():
        deleted.append(True)

    with pytest.raises(HolonError) as exc:
        asyncio.run(
            deps._seed_tool_plugin_authz(
                tenant_id="acme", name="p", compensate_delete=_compensate
            )
        )
    assert exc.value.error_name == "AuthzSeedFailed"
    assert deleted == [True]


def test_seed_failure_keeps_preexisting_row(monkeypatch) -> None:
    _failing_plugin_seed(monkeypatch)
    with pytest.raises(HolonError):
        asyncio.run(
            deps._seed_tool_plugin_authz(
                tenant_id="acme", name="p", compensate_delete=None
            )
        )


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
    {"name": "a"},
    {"name": "b"},
    {"name": "c"},
]


@pytest.mark.parametrize("lookup_fails", [False, True])
def test_filter_readable_keeps_only_granted_rows(monkeypatch, lookup_fails) -> None:
    fake = _FakeAuthz(
        readable={ml_model_urn("acme", "a"), ml_model_urn("acme", "c")},
        lookup_fails=lookup_fails,
    )
    monkeypatch.setattr(deps, "authz", fake)
    kept = asyncio.run(
        deps._filter_readable(
            _ListPrincipal(),
            "ml_model",
            _ROWS,
            urn_fn=lambda row: ml_model_urn("acme", row["name"]),
        )
    )
    assert [row["name"] for row in kept] == ["a", "c"]


def test_filter_readable_intersects_mandant(monkeypatch) -> None:
    fake = _FakeAuthz(
        readable={ml_model_urn("acme", "a"), ml_model_urn("acme", "b")},
        mandant_readable={ml_model_urn("acme", "b")},
    )
    monkeypatch.setattr(deps, "authz", fake)
    kept = asyncio.run(
        deps._filter_readable(
            _ListPrincipal("mandant"),
            "ml_model",
            _ROWS,
            urn_fn=lambda row: ml_model_urn("acme", row["name"]),
        )
    )
    assert [row["name"] for row in kept] == ["b"]


def test_unlink_uses_global_urn_segment(monkeypatch) -> None:
    fake = _FakeAuthz()
    monkeypatch.setattr(deps, "authz", fake)
    asyncio.run(deps._unlink_resource_authz("tool_plugin", tenant_id="acme", name="p"))
    assert fake.deleted == [tool_plugin_urn("acme", "p")]


def test_unlink_agent_session_uses_full_urn(monkeypatch) -> None:
    fake = _FakeAuthz()
    monkeypatch.setattr(deps, "authz", fake)
    session_urn = build_urn("acme", "global", "agent-session", "abc123")
    asyncio.run(deps._unlink_agent_session_authz(tenant_id="acme", session_urn=session_urn))
    assert fake.deleted == [session_urn]


class _FakeConn:
    def __init__(self, tables):
        self.tables = tables

    async def fetch(self, sql):
        if "agent_session" in sql:
            return self.tables.get("agent_session", [])
        if "plugin_registration" in sql:
            return self.tables.get("plugin_registration", [])
        if "model_registration" in sql:
            return self.tables.get("model_registration", [])
        return []


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
    "agent_session": [
        {"urn": "hl:acme:global:agent-session:s1", "tenant_id": "acme"},
    ],
    "plugin_registration": [{"name": "weather"}],
    "model_registration": [{"name": "clf", "tenant_id": "acme"}],
}


def test_backfill_runs_once() -> None:
    fake = _FakeAuthz()
    pool = _FakePool(_TABLES)
    asyncio.run(authz_seed.ensure_authz_seeded(fake, pool))
    assert set(fake.written) == {
        "hl:acme:global:agent-session:s1",
        tool_plugin_urn("acme", "weather"),
        ml_model_urn("acme", "clf"),
    }
    assert pool.done

    fake.written.clear()
    asyncio.run(authz_seed.ensure_authz_seeded(fake, pool))
    assert fake.written == []


def test_backfill_not_marked_done_on_failure(monkeypatch) -> None:
    async def _fail(*args, **kwargs):
        raise RuntimeError("spicedb down")

    monkeypatch.setattr(authz_seed, "seed_ml_model_parent_workspace", _fail)
    pool = _FakePool(_TABLES)
    asyncio.run(authz_seed.ensure_authz_seeded(_FakeAuthz(), pool))
    assert not pool.done
