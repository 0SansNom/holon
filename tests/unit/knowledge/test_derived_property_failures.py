"""A derived property that fails to compute is named in `_failedDerivedFields`, not silently dropped."""

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
os.environ.setdefault("HOLON_JWT_SECRET", "unit-test-jwt-secret-must-be-long")
os.environ.setdefault("HOLON_ICEBERG_CATALOG_URI", "http://localhost:8181")
os.environ.setdefault("HOLON_ICEBERG_WAREHOUSE", "s3://warehouse")
os.environ.setdefault("HOLON_S3_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_REGION", "us-east-1")

REPO_ROOT = Path(__file__).resolve().parents[3]
_STUBBED_ELSEWHERE = ("app", "holon_common", "pyiceberg", "asyncpg", "httpx", "prometheus_client")
_URN = "hl:acme:main:object-type:Customer"


def _is_stubbable(name: str) -> bool:
    return name.split(".")[0] in _STUBBED_ELSEWHERE


@pytest.fixture
def core():
    """Import the real `app.core`; other unit modules stub `app.*` and its deps in sys.modules."""
    saved = {name: module for name, module in sys.modules.items() if _is_stubbable(name)}
    for name in saved:
        del sys.modules[name]
    sys.modules.setdefault("duckdb", types.ModuleType("duckdb"))
    try:
        with patch.object(sys, "path", [str(REPO_ROOT / "services" / "knowledge"), str(REPO_ROOT / "libs"), *sys.path]):
            yield importlib.import_module("app.core")
    finally:
        for name in [name for name in sys.modules if _is_stubbable(name)]:
            del sys.modules[name]
        sys.modules.update(saved)


class _Plugin:
    async def call(self, **inputs):
        if inputs["revenue"] == 0:
            raise RuntimeError("model unavailable")
        return {"score": inputs["revenue"] * 2}


def _patch_definition(core, monkeypatch, *, functions: dict) -> None:
    async def get_object_type(pool, urn):
        return {
            "property_mapping": {"revenue": "revenue", "region": "region"},
            "derived_properties": {"score": "score_fn"},
        }

    async def find_active_function_by_name(pool, name):
        return functions.get(name)

    monkeypatch.setattr(core.ontology, "get_object_type", get_object_type)
    monkeypatch.setattr(core.function_registry, "find_active_function_by_name", find_active_function_by_name)
    monkeypatch.setattr(core.function_registry, "load_function_plugin", lambda manifest: _Plugin())


def _derive(core, rows: list[dict]) -> list[dict]:
    return asyncio.run(core._apply_derived_properties(_URN, rows, principal=None))


_REGISTRATION = {"manifest": {"function_name": "score_fn", "input_schema": {"required": ["revenue"]}}}


def test_plugin_error_is_reported_on_the_failing_row_only(core, monkeypatch) -> None:
    _patch_definition(core, monkeypatch, functions={"score_fn": _REGISTRATION})

    ok, broken = _derive(core, [{"id": 1, "revenue": 5}, {"id": 2, "revenue": 0}])

    assert ok["score"] == 10
    assert "_failedDerivedFields" not in ok
    assert "score" not in broken
    assert broken["_failedDerivedFields"] == ["score"]


def test_missing_function_is_reported_on_every_row(core, monkeypatch) -> None:
    _patch_definition(core, monkeypatch, functions={})

    rows = _derive(core, [{"id": 1, "revenue": 5}, {"id": 2, "revenue": 7}])

    assert [row.get("_failedDerivedFields") for row in rows] == [["score"], ["score"]]


def test_masked_required_input_is_not_a_failure(core, monkeypatch) -> None:
    _patch_definition(core, monkeypatch, functions={"score_fn": _REGISTRATION})

    (row,) = _derive(core, [{"id": 1, "revenue": None, "_maskedFields": ["revenue"]}])

    assert "score" not in row
    assert "_failedDerivedFields" not in row
