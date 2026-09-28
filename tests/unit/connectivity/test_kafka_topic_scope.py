"""A stream topic is either `{tenant}.…` or already bound to that tenant."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.modules.setdefault("asyncpg", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app.kafka_stream_registry import (  # noqa: E402
    KafkaStreamConflictError,
    assert_topic_available,
    register_source,
)
from holon_common.connector_safety import ConnectorSafetyError  # noqa: E402


def test_register_rejects_another_tenants_topic() -> None:
    pool = MagicMock()
    pool.fetchrow = AsyncMock()
    pool.execute = AsyncMock()
    with pytest.raises(KafkaStreamConflictError, match="acme."):
        asyncio.run(
            register_source(
                pool,
                tenant_id="acme",
                name="orders-stream",
                topic="beta.orders",
                key_field="id",
                dataset_name="orders",
                batch_interval_seconds=5,
                created_by_urn="urn:jdoe",
            )
        )
    pool.execute.assert_not_called()


def test_consume_recheck_rejects_a_topic_bound_to_another_tenant() -> None:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"tenant_id": "beta"})
    with pytest.raises(ConnectorSafetyError, match="another tenant"):
        asyncio.run(assert_topic_available(pool, tenant_id="acme", topic="acme.orders"))
