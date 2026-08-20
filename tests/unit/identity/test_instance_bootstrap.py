"""Tests for Instance Bootstrap."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "identity"))

from app.seed import ensure_instance_bootstrap  # noqa: E402


def _pool(*, fetchrow=None, fetch=None):
    pool = MagicMock()
    if callable(fetchrow):
        pool.fetchrow = AsyncMock(side_effect=fetchrow)
    else:
        pool.fetchrow = AsyncMock(return_value=fetchrow)
    if callable(fetch):
        pool.fetch = AsyncMock(side_effect=fetch)
    else:
        pool.fetch = AsyncMock(return_value=fetch if fetch is not None else [])
    pool.execute = AsyncMock(return_value="UPDATE 1")
    return pool


def _authz(*, admin_rels=None):
    authz = MagicMock()
    authz.write_relationship = AsyncMock()
    authz.read_relationships = AsyncMock(return_value=admin_rels or [])
    return authz


@pytest.fixture(autouse=True)
def _bootstrap_env(monkeypatch):
    monkeypatch.setenv("HOLON_BOOTSTRAP_ADMIN_SECRET", "test-bootstrap-secret")
    monkeypatch.delenv("HOLON_BOOTSTRAP_ADMIN_RESET_SECRET", raising=False)
    monkeypatch.setenv("HOLON_BOOTSTRAP_ADMIN_LOCAL_NAME", "admin")


def test_empty_db_creates_tenant_workspace_admin_and_grants():
    rows = [None, None, None]

    async def fetchrow(*_a, **_k):
        return rows.pop(0) if rows else None

    pool = _pool(fetchrow=fetchrow, fetch=[])
    authz = _authz(admin_rels=[])

    async def _body() -> None:
        await ensure_instance_bootstrap(pool, authz, tenant_id="acme", workspace_id="main")

    asyncio.run(_body())

    assert pool.execute.await_count >= 3
    relations = {(c.kwargs["resource_type"], c.kwargs["relation"]) for c in authz.write_relationship.await_args_list}
    assert ("workspace", "parent_tenant") in relations
    assert ("tenant", "member") in relations
    assert ("workspace", "admin") in relations


def test_usable_admin_skips_principal_create_but_touches_parent_tenant():
    admin_urn = "hl:acme:global:user:jdoe"
    oid = admin_urn.replace(":", "_").replace(".", "_")

    async def fetchrow(query, *args):
        if "FROM tenant" in query:
            return {"tenant_id": "acme"}
        if "FROM workspace" in query:
            return {"workspace_id": "main", "tenant_id": "acme", "status": "active"}
        return None

    pool = _pool(fetchrow=fetchrow, fetch=[{"urn": admin_urn, "status": "active"}])
    authz = _authz(
        admin_rels=[
            {"relation": "admin", "subject": {"object": {"objectType": "principal", "objectId": oid}}},
        ]
    )

    async def _body() -> None:
        await ensure_instance_bootstrap(pool, authz, tenant_id="acme", workspace_id="main")

    asyncio.run(_body())

    insert_sqls = [c.args[0] for c in pool.execute.await_args_list if c.args]
    assert not any("INSERT INTO principal" in s for s in insert_sqls)
    assert authz.write_relationship.await_count == 1
    assert authz.write_relationship.await_args.kwargs["relation"] == "parent_tenant"


def test_orphan_tenant_without_usable_admin_repairs_bootstrap_admin(monkeypatch):
    monkeypatch.setenv("HOLON_BOOTSTRAP_ADMIN_SECRET", "break-glass-secret")

    async def fetchrow(query, *args):
        if "FROM tenant" in query:
            return {"tenant_id": "acme"}
        if "FROM workspace" in query:
            return {"workspace_id": "main", "tenant_id": "acme", "status": "active"}
        if "FROM principal" in query:
            return None
        return None

    pool = _pool(fetchrow=fetchrow, fetch=[])
    authz = _authz(admin_rels=[])

    async def _body() -> None:
        await ensure_instance_bootstrap(pool, authz, tenant_id="acme", workspace_id="main")

    asyncio.run(_body())

    insert_sqls = [c.args[0] for c in pool.execute.await_args_list if c.args]
    assert any("INSERT INTO principal" in s for s in insert_sqls)
    relations = {c.kwargs["relation"] for c in authz.write_relationship.await_args_list}
    assert relations >= {"parent_tenant", "member", "admin"}


def test_stale_spicedb_admin_without_principal_row_triggers_repair():
    async def fetchrow(query, *args):
        if "FROM tenant" in query:
            return {"tenant_id": "acme"}
        if "FROM workspace" in query:
            return {"workspace_id": "main", "tenant_id": "acme"}
        if "FROM principal" in query:
            return None
        return None

    pool = _pool(fetchrow=fetchrow, fetch=[])
    authz = _authz(
        admin_rels=[
            {
                "relation": "admin",
                "subject": {"object": {"objectType": "principal", "objectId": "hl_ghost_global_user_x"}},
            },
        ]
    )

    async def _body() -> None:
        await ensure_instance_bootstrap(pool, authz, tenant_id="acme", workspace_id="main")

    asyncio.run(_body())
    assert any("INSERT INTO principal" in (c.args[0] if c.args else "") for c in pool.execute.await_args_list)


def test_workspace_tenant_mismatch_fails_closed():
    async def fetchrow(query, *args):
        if "FROM tenant" in query:
            return {"tenant_id": "acme"}
        if "FROM workspace" in query:
            return {"workspace_id": "main", "tenant_id": "other"}
        return None

    pool = _pool(fetchrow=fetchrow, fetch=[])
    authz = _authz()

    async def _body() -> None:
        await ensure_instance_bootstrap(pool, authz, tenant_id="acme", workspace_id="main")

    with pytest.raises(RuntimeError, match="belongs to tenant"):
        asyncio.run(_body())


def test_empty_instance_requires_bootstrap_secret_in_every_env(monkeypatch):
    monkeypatch.delenv("HOLON_BOOTSTRAP_ADMIN_SECRET", raising=False)

    pool = _pool(fetchrow=AsyncMock(return_value=None), fetch=[])
    authz = _authz(admin_rels=[])

    async def _body() -> None:
        await ensure_instance_bootstrap(pool, authz, tenant_id="acme", workspace_id="main")

    with pytest.raises(RuntimeError, match="HOLON_BOOTSTRAP_ADMIN_SECRET"):
        asyncio.run(_body())


def test_reset_secret_updates_existing_bootstrap_admin(monkeypatch):
    monkeypatch.setenv("HOLON_BOOTSTRAP_ADMIN_SECRET", "new-secret")
    monkeypatch.setenv("HOLON_BOOTSTRAP_ADMIN_RESET_SECRET", "true")
    admin_urn = "hl:acme:global:user:admin"
    oid = admin_urn.replace(":", "_").replace(".", "_")

    async def fetchrow(query, *args):
        if "FROM tenant" in query:
            return {"tenant_id": "acme"}
        if "FROM workspace" in query:
            return {"workspace_id": "main", "tenant_id": "acme"}
        if "FROM principal" in query:
            return {"urn": admin_urn, "status": "active", "client_secret": "old"}
        return None

    pool = _pool(fetchrow=fetchrow, fetch=[{"urn": admin_urn, "status": "active"}])
    authz = _authz(
        admin_rels=[
            {"relation": "admin", "subject": {"object": {"objectType": "principal", "objectId": oid}}},
        ]
    )

    async def _body() -> None:
        await ensure_instance_bootstrap(pool, authz, tenant_id="acme", workspace_id="main")

    asyncio.run(_body())

    updates = [
        c.args
        for c in pool.execute.await_args_list
        if c.args and "UPDATE principal SET client_secret" in c.args[0]
    ]
    assert updates
    assert updates[0][1] == "new-secret"
    assert updates[0][2] == admin_urn
