"""Reading the knowledge pool before lifespan is a 503, not AttributeError."""

from __future__ import annotations

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


def _is_stubbable(name: str) -> bool:
    return name.split(".")[0] in _STUBBED_ELSEWHERE


@pytest.fixture
def knowledge_core():
    """Import real knowledge `app.core` without a stale `app` from another service."""
    saved = {name: module for name, module in sys.modules.items() if _is_stubbable(name)}
    for name in saved:
        del sys.modules[name]
    sys.modules.setdefault("duckdb", types.ModuleType("duckdb"))
    try:
        with patch.object(
            sys,
            "path",
            [str(REPO_ROOT / "services" / "knowledge"), str(REPO_ROOT / "libs"), *sys.path],
        ):
            yield importlib.import_module("app.core")
    finally:
        for name in [name for name in sys.modules if _is_stubbable(name)]:
            del sys.modules[name]
        sys.modules.update(saved)


def test_pool_access_before_lifespan_is_unavailable(knowledge_core) -> None:
    from holon_common import HolonError

    core = knowledge_core
    if not isinstance(core.pool, core._NotReady):
        return
    try:
        core.require_pool()
        raise AssertionError("expected KnowledgeNotReady")
    except HolonError as exc:
        assert exc.status_code == 503
    try:
        core.pool.fetch("SELECT 1")
        raise AssertionError("expected KnowledgeNotReady")
    except HolonError as exc:
        assert exc.status_code == 503
