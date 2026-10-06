"""A read by key tells "type not materialized yet" apart from "instance absent"."""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("HOLON_TENANT_ID", "acme")
os.environ.setdefault("HOLON_WORKSPACE_ID", "main")
os.environ.setdefault("HOLON_JWT_SECRET", "unit-test-jwt-secret-must-be-long")
os.environ.setdefault("HOLON_ICEBERG_CATALOG_URI", "http://localhost:8181")
os.environ.setdefault("HOLON_ICEBERG_WAREHOUSE", "s3://warehouse")
os.environ.setdefault("HOLON_S3_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_REGION", "us-east-1")

REPO_ROOT = Path(__file__).resolve().parents[3]
_STUBBED_ELSEWHERE = ("app", "holon_common", "pyiceberg", "asyncpg", "httpx", "prometheus_client")


def _is_stubbable(name: str) -> bool:
    return name.split(".")[0] in _STUBBED_ELSEWHERE


@pytest.fixture
def knowledge():
    """Import the real `app.core` and `app.serving_store`; other unit modules stub `app.*` in sys.modules."""
    saved = {name: module for name, module in sys.modules.items() if _is_stubbable(name)}
    for name in saved:
        del sys.modules[name]
    sys.modules.setdefault("duckdb", types.ModuleType("duckdb"))
    try:
        with patch.object(sys, "path", [str(REPO_ROOT / "services" / "knowledge"), str(REPO_ROOT / "libs"), *sys.path]):
            yield types.SimpleNamespace(
                core=importlib.import_module("app.core"),
                serving_store=importlib.import_module("app.serving_store"),
            )
    finally:
        for name in [name for name in sys.modules if _is_stubbable(name)]:
            del sys.modules[name]
        sys.modules.update(saved)


def _miss(knowledge, monkeypatch, *, materialized: bool, as_of=None):
    looked_up: list[tuple[str, str]] = []

    async def is_materialized(pool, object_type, tenant_id):
        looked_up.append((object_type, tenant_id))
        return materialized

    monkeypatch.setattr(knowledge.core.serving_store, "is_materialized", is_materialized)
    error = asyncio.run(knowledge.core.instance_not_found("Customer", "acme", "42", as_of=as_of))
    return error, looked_up


def test_type_never_materialized_has_its_own_error_name(knowledge, monkeypatch) -> None:
    error, looked_up = _miss(knowledge, monkeypatch, materialized=False)

    assert error.status_code == 404
    assert error.error_name == "ObjectTypeNotMaterialized"
    assert looked_up == [("Customer", "acme")]


def test_materialized_type_reports_an_absent_instance(knowledge, monkeypatch) -> None:
    error, _ = _miss(knowledge, monkeypatch, materialized=True)

    assert error.status_code == 404
    assert error.error_name == "ObjectInstanceNotFound"
    assert error.detail == "Customer/42 not found"


def test_historical_read_keeps_the_as_of_detail(knowledge, monkeypatch) -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    error, looked_up = _miss(knowledge, monkeypatch, materialized=False, as_of=as_of)

    assert error.error_name == "ObjectInstanceNotFound"
    assert "as of 2026-01-01" in error.detail
    assert looked_up == []


def test_an_empty_snapshot_still_records_the_type_as_materialized(knowledge) -> None:
    statements: list[tuple[str, tuple]] = []

    class _Conn:
        async def execute(self, sql: str, *args):
            statements.append((sql, args))

    asyncio.run(
        knowledge.serving_store.materialize(_Conn(), object_type="Customer", tenant_id="acme", snapshot_id=9, rows=[])
    )

    marker = [args for sql, args in statements if "object_type_materialization" in sql]
    assert marker == [("Customer", "acme", 9)]
