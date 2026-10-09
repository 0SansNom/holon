"""Shared workspace/project grant tail, and workspace listing fallback."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Identity Pydantic models use PEP 604 unions (`str | None`); CI runs 3.11+.
if sys.version_info < (3, 10):
    pytest.skip("identity deps models need Python 3.10+", allow_module_level=True)

os.environ.setdefault("HOLON_TENANT_ID", "acme")
os.environ.setdefault("HOLON_WORKSPACE_ID", "main")
os.environ.setdefault("HOLON_JWT_SECRET", "unit-test-jwt-secret-must-be-long")
os.environ.setdefault("HOLON_DB_URL", "postgresql://localhost/identity")
os.environ.setdefault("HOLON_SPICEDB_URL", "http://localhost:8443")
os.environ.setdefault("HOLON_SPICEDB_PRESHARED_KEY", "test")
os.environ.setdefault("HOLON_SPICEDB_SCHEMA_PATH", "schema.zed")
os.environ.setdefault("HOLON_OPA_URL", "http://localhost:8181")
os.environ.setdefault("HOLON_KAFKA_BOOTSTRAP", "localhost:9092")

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "identity"))

from holon_common import HolonError, Principal  # noqa: E402
from app import access_ops, deps  # noqa: E402
from app.routers import directory  # noqa: E402


def _principal(urn: str, type_: str = "user") -> Principal:
    return Principal(urn=urn, type=type_, tenant_id="acme", display_name=urn.rsplit(":", 1)[-1])


def _install_runtime() -> MagicMock:
    conn = MagicMock()
    tx = AsyncMock()
    tx.__aenter__.return_value = None
    tx.__aexit__.return_value = False
    conn.transaction = MagicMock(return_value=tx)
    acquire = AsyncMock()
    acquire.__aenter__.return_value = conn
    acquire.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire)
    pool.fetch = AsyncMock(return_value=[])
    authz = MagicMock()
    authz.write_relationship = AsyncMock()
    authz.delete_relationship = AsyncMock()
    authz.read_relationships = AsyncMock(return_value=[])
    deps.pool = pool
    deps.authz = authz
    return authz


def test_project_grant_writes_relationship_event_and_audit(monkeypatch) -> None:
    authz = _install_runtime()
    audits: list[dict] = []
    enqueued: list = []

    monkeypatch.setattr(access_ops, "emit_audit", lambda **kwargs: audits.append(kwargs))

    async def capture_enqueue(_conn, event) -> None:
        enqueued.append(event)

    monkeypatch.setattr(access_ops.outbox, "enqueue", capture_enqueue)
    actor = _principal("hl:acme:global:user:admin")
    target = _principal("hl:acme:global:user:alice")

    asyncio.run(
        deps._apply_access_change(
            granted=True,
            target=target,
            resource_type="project",
            resource_urn="hl:acme:main:project:billing",
            relation="viewer",
            actor=actor,
            tenant_id="acme",
        )
    )

    authz.write_relationship.assert_awaited_once()
    authz.delete_relationship.assert_not_awaited()
    authz.read_relationships.assert_not_awaited()
    assert enqueued[0].event_type == "identity.permission.granted"
    assert enqueued[0].workspace_id == "main"
    assert enqueued[0].payload["resource_type"] == "project"
    assert audits[0]["action"] == "identity.permission.granted"
    assert audits[0]["resource_type"] == "project"
    assert audits[0]["permission"] == "viewer"
    assert audits[0]["reason"] == "granted viewer to hl:acme:global:user:alice"


def test_group_revoke_fans_out_members(monkeypatch) -> None:
    authz = _install_runtime()
    monkeypatch.setattr(access_ops, "emit_audit", lambda **kwargs: None)
    monkeypatch.setattr(access_ops.outbox, "enqueue", AsyncMock())
    actor = _principal("hl:acme:global:user:admin")
    group = _principal("hl:acme:global:group:finance", type_="group")

    asyncio.run(
        deps._apply_access_change(
            granted=False,
            target=group,
            resource_type="workspace",
            resource_urn="hl:acme:global:workspace:ops",
            relation="editor",
            actor=actor,
            tenant_id="acme",
            workspace_id="ops",
        )
    )

    authz.delete_relationship.assert_awaited_once()
    authz.write_relationship.assert_not_awaited()
    authz.read_relationships.assert_awaited_once()


def test_workspaces_list_falls_back_to_caller_tenant_on_403(monkeypatch) -> None:
    principal = _principal("hl:acme:global:user:alice")
    seen: list = []

    async def deny(_principal) -> None:
        raise HolonError.forbidden("PermissionDenied", "no")

    async def listing(_pool, tenant_id):
        seen.append(tenant_id)
        return [{"workspace_id": "main"}]

    monkeypatch.setattr(directory, "_authorize_bootstrap_governance", deny)
    monkeypatch.setattr(directory, "list_workspaces", listing)

    rows = asyncio.run(directory.workspaces_list(tenant_id="other", principal=principal))

    assert rows == [{"workspace_id": "main"}]
    assert seen == ["acme"]


def test_workspaces_list_propagates_authz_outage(monkeypatch) -> None:
    principal = _principal("hl:acme:global:user:alice")

    async def down(_principal) -> None:
        raise HolonError.unavailable("SpiceDbUnavailable", "down")

    monkeypatch.setattr(directory, "_authorize_bootstrap_governance", down)

    with pytest.raises(HolonError) as exc:
        asyncio.run(directory.workspaces_list(principal=principal))
    assert exc.value.status_code == 503
