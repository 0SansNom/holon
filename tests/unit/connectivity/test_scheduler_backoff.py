"""A failed sync waits out its interval before it is due again."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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

REPO = Path(__file__).resolve().parents[3]
sys.modules.setdefault("asyncpg", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app.ingest_scheduler import _LAST_PIPELINE_SQL, _LAST_SYNC_SQL, _is_due  # noqa: E402

pytestmark = pytest.mark.unit


def test_a_recent_failure_is_not_due() -> None:
    async def scenario() -> None:
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=datetime.now(timezone.utc))
        due = await _is_due(pool, _LAST_SYNC_SQL, "acme", "hl:acme:main:dataset:orders", timedelta(minutes=15))
        assert due is False
        sql = pool.fetchval.await_args.args[0]
        assert "sync_failure" in sql
        assert "sync_run" in sql

    asyncio.run(scenario())


def test_never_run_is_due() -> None:
    async def scenario() -> None:
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=None)
        due = await _is_due(pool, _LAST_SYNC_SQL, "acme", "hl:acme:main:dataset:orders", timedelta(minutes=15))
        assert due is True

    asyncio.run(scenario())


def test_pipeline_due_check_includes_failed_runs() -> None:
    assert "status = 'succeeded'" not in _LAST_PIPELINE_SQL
