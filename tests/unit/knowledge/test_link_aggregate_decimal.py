"""Link sums use decimals, and a page of parents is one grouped query."""

from __future__ import annotations

import asyncio
import os
import sys
import types
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("HOLON_TENANT_ID", "acme")
os.environ.setdefault("HOLON_WORKSPACE_ID", "main")
os.environ.setdefault("HOLON_JWT_SECRET", "unit-test-secret")
os.environ.setdefault("HOLON_ICEBERG_CATALOG_URI", "http://localhost:8181")
os.environ.setdefault("HOLON_ICEBERG_WAREHOUSE", "s3://holon-warehouse/")
os.environ.setdefault("HOLON_S3_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "holon")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "holon")
os.environ.setdefault("AWS_REGION", "us-east-1")

sys.modules.setdefault("duckdb", types.ModuleType("duckdb"))

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "services" / "knowledge"))
sys.path.insert(0, str(REPO / "libs"))

import app.core  # noqa: E402, F401 — loads derived through core; importing derived first is a cycle
from app.derived import _combine_fk_aggregate, _reduce_numeric  # noqa: E402
from app.serving_store import aggregate_fk_children  # noqa: E402

pytestmark = pytest.mark.unit


def test_sum_of_tenths_is_exact() -> None:
    assert _reduce_numeric(["0.10", "0.20"], "sum") == Decimal("0.30")
    assert _reduce_numeric(["0.10", "0.20"], "avg") == Decimal("0.15")


def test_grouped_counts_are_filled_in_for_every_parent() -> None:
    grouped = [{"parent_id": "a", "n": 2, "total": None, "min_v": None, "max_v": None, "numeric_n": 0, "bad": 0}]
    result = _combine_fk_aggregate("count", ["a", "b"], grouped, [], set())
    assert result == {"a": 2, "b": 0}


def test_aggregate_fk_children_groups_once() -> None:
    async def scenario() -> None:
        pool = MagicMock()
        pool.fetch = AsyncMock(side_effect=[[], []])
        grouped, marked = await aggregate_fk_children(
            pool,
            child_object_type="Order",
            tenant_id="acme",
            child_object_type_urn="hl:acme:main:object-type:Order",
            fk_column="customer_id",
            parent_ids=["c1", "c2"],
            value_column="amount",
        )
        assert grouped == []
        assert marked == []
        assert pool.fetch.await_count == 2
        sql = pool.fetch.await_args_list[0].args[0]
        assert "GROUP BY" in sql
        assert "ANY" in sql

    asyncio.run(scenario())
