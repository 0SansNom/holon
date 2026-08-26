"""Tests for deep /ready dependency probes."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "libs"))

from holon_common.errors import HolonError
from holon_common.readiness import (
    CheckResult,
    check_postgres,
    first_kafka_broker,
    report_ready,
)


class _OkPool:
    async def fetchval(self, _sql: str) -> int:
        return 1


class _DownPool:
    async def fetchval(self, _sql: str) -> int:
        raise RuntimeError("connection refused")


def test_first_kafka_broker_parses_host_port() -> None:
    assert first_kafka_broker("redpanda:9092") == ("redpanda", 9092)
    assert first_kafka_broker("kafka-1:9092,kafka-2:9092") == ("kafka-1", 9092)


def test_first_kafka_broker_rejects_junk() -> None:
    with pytest.raises(ValueError, match="invalid kafka bootstrap"):
        first_kafka_broker("not-a-broker")


def test_check_postgres_ok() -> None:
    result = asyncio.run(check_postgres(_OkPool()))
    assert result == CheckResult("postgres", True)


def test_check_postgres_down() -> None:
    result = asyncio.run(check_postgres(_DownPool()))
    assert result.name == "postgres"
    assert result.ok is False
    assert "connection refused" in (result.error or "")


def test_report_ready_ok_includes_checks_and_extra() -> None:
    async def _ok() -> CheckResult:
        return CheckResult("postgres", True)

    payload = asyncio.run(report_ready([_ok()], extra={"quiesced": False}))
    assert payload == {"status": "ok", "checks": {"postgres": "ok"}, "quiesced": False}


def test_report_ready_failure_is_unavailable() -> None:
    async def _ok() -> CheckResult:
        return CheckResult("postgres", True)

    async def _bad() -> CheckResult:
        return CheckResult("spicedb", False, "connection refused")

    with pytest.raises(HolonError) as exc:
        asyncio.run(report_ready([_ok(), _bad()]))
    assert exc.value.status_code == 503
    assert exc.value.error_name == "NotReady"
    assert exc.value.parameters["failed"] == ["spicedb"]
    assert exc.value.parameters["checks"]["postgres"] == "ok"
    assert "connection refused" in exc.value.parameters["checks"]["spicedb"]
