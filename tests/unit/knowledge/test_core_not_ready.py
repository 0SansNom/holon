"""Reading the knowledge pool before lifespan is a 503, not AttributeError."""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

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
sys.path.insert(0, str(REPO_ROOT / "libs"))
sys.path.insert(0, str(REPO_ROOT / "services" / "knowledge"))
sys.modules.setdefault("duckdb", types.ModuleType("duckdb"))

from holon_common import HolonError  # noqa: E402


def test_pool_access_before_lifespan_is_unavailable() -> None:
    import app.core as core

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
