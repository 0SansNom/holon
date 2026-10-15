"""An approval whose action type was deleted stays on the list."""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("HOLON_TENANT_ID", "acme")
os.environ.setdefault("HOLON_WORKSPACE_ID", "main")
os.environ.setdefault("HOLON_JWT_SECRET", "unit-test-secret")
os.environ.setdefault("HOLON_DB_URL", "postgresql://holon:holon@localhost:5432/holon_knowledge")
os.environ.setdefault("HOLON_IDENTITY_URL", "http://localhost:8001")
os.environ.setdefault("HOLON_KAFKA_BOOTSTRAP", "localhost:9092")
os.environ.setdefault("HOLON_OPENSEARCH_URL", "http://localhost:9200")
os.environ.setdefault("HOLON_OPENSEARCH_PASSWORD", "admin")
os.environ.setdefault("HOLON_SPICEDB_URL", "http://localhost:8443")
os.environ.setdefault("HOLON_SPICEDB_PRESHARED_KEY", "change-me")
os.environ.setdefault("HOLON_OPA_URL", "http://localhost:8181")
os.environ.setdefault("HOLON_ICEBERG_CATALOG_URI", "http://localhost:8181")
os.environ.setdefault("HOLON_ICEBERG_WAREHOUSE", "s3://holon-warehouse/")
os.environ.setdefault("HOLON_S3_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "holon")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "holon")
os.environ.setdefault("AWS_REGION", "us-east-1")
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_STUBBED_ELSEWHERE = ("app", "holon_common", "pyiceberg", "asyncpg", "httpx", "prometheus_client")


def _is_stubbable(name: str) -> bool:
    return name.split(".")[0] in _STUBBED_ELSEWHERE


@pytest.fixture
def actions_router():
    saved = {name: module for name, module in sys.modules.items() if _is_stubbable(name)}
    for name in saved:
        del sys.modules[name]
    sys.modules.setdefault("duckdb", types.ModuleType("duckdb"))
    try:
        sys.path.insert(0, str(REPO_ROOT / "libs"))
        sys.path.insert(0, str(REPO_ROOT / "services" / "knowledge"))
        yield importlib.import_module("app.routers.actions")
    finally:
        for name in [name for name in sys.modules if _is_stubbable(name)]:
            del sys.modules[name]
        sys.modules.update(saved)


def test_deleted_action_stays_visible(actions_router, monkeypatch: pytest.MonkeyPatch) -> None:
    row = {"id": 7, "tenant_id": "acme", "action_name": "gone", "status": "pending"}
    monkeypatch.setattr(actions_router.actions, "list_approvals", AsyncMock(return_value=[row]))
    monkeypatch.setattr(actions_router.actions, "_get_action_definition", AsyncMock(return_value=None))
    principal = MagicMock()
    principal.tenant_id = "acme"

    async def run():
        return await actions_router.list_pending_approvals(status=None, principal=principal)

    listed = asyncio.run(run())
    assert listed == [{**row, "orphaned": True}]
