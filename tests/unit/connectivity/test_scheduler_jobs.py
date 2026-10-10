"""Scheduled jobs run in the background, capped per tenant and globally."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "connectivity_scheduler_jobs", ROOT / "services" / "connectivity" / "app" / "scheduler_jobs.py"
)
scheduler_jobs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scheduler_jobs)

pytestmark = pytest.mark.unit


class _Recorder:
    def __init__(self) -> None:
        self.running: dict[str, int] = {}
        self.peak: dict[str, int] = {}
        self.peak_total = 0
        self.finished: list[str] = []

    def job(self, tenant_id: str, name: str, gate: asyncio.Event):
        async def run() -> None:
            self.running[tenant_id] = self.running.get(tenant_id, 0) + 1
            self.peak[tenant_id] = max(self.peak.get(tenant_id, 0), self.running[tenant_id])
            self.peak_total = max(self.peak_total, sum(self.running.values()))
            try:
                await gate.wait()
            finally:
                self.running[tenant_id] -= 1
            self.finished.append(name)

        return run


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def test_a_slow_tenant_does_not_hold_back_another_tenant() -> None:
    async def scenario() -> None:
        runner = scheduler_jobs.ScheduledJobRunner(max_concurrency=4, tenant_concurrency=2)
        recorder = _Recorder()
        slow = asyncio.Event()
        fast = asyncio.Event()
        for index in range(5):
            runner.submit(("sync", "acme", f"big-{index}"), "acme", recorder.job("acme", f"big-{index}", slow))
        runner.submit(("sync", "globex", "orders"), "globex", recorder.job("globex", "orders", fast))
        await _settle()

        assert recorder.running == {"acme": 2, "globex": 1}
        fast.set()
        await _settle()
        assert recorder.finished == ["orders"]

        slow.set()
        while len(recorder.finished) < 6:
            await asyncio.sleep(0)
        assert recorder.peak == {"acme": 2, "globex": 1}

    asyncio.run(scenario())


def test_global_cap_holds_across_tenants() -> None:
    async def scenario() -> None:
        runner = scheduler_jobs.ScheduledJobRunner(max_concurrency=3, tenant_concurrency=2)
        recorder = _Recorder()
        gate = asyncio.Event()
        for tenant_id in ("a", "b", "c", "d"):
            for index in range(2):
                runner.submit(("sync", tenant_id, str(index)), tenant_id, recorder.job(tenant_id, f"{tenant_id}{index}", gate))
        await _settle()
        assert sum(recorder.running.values()) == 3
        gate.set()
        while len(recorder.finished) < 8:
            await asyncio.sleep(0)
        assert recorder.peak_total == 3

    asyncio.run(scenario())


def test_a_job_still_running_is_not_started_twice() -> None:
    async def scenario() -> None:
        runner = scheduler_jobs.ScheduledJobRunner(max_concurrency=2, tenant_concurrency=2)
        recorder = _Recorder()
        gate = asyncio.Event()
        key = ("sync", "acme", "orders")
        assert runner.submit(key, "acme", recorder.job("acme", "first", gate)) is True
        await _settle()
        assert runner.submit(key, "acme", recorder.job("acme", "second", gate)) is False
        gate.set()
        await _settle()
        runner.reap()
        assert not runner.is_running(key)
        assert runner.submit(key, "acme", recorder.job("acme", "third", gate)) is True
        await _settle()
        assert recorder.finished == ["first", "third"]

    asyncio.run(scenario())


def test_a_failing_job_frees_its_slots() -> None:
    async def scenario() -> None:
        runner = scheduler_jobs.ScheduledJobRunner(max_concurrency=1, tenant_concurrency=1)
        ran: list[str] = []

        async def boom() -> None:
            raise RuntimeError("source down")

        async def ok() -> None:
            ran.append("ok")

        runner.submit("boom", "acme", boom)
        runner.submit("ok", "acme", ok)
        await _settle()
        assert ran == ["ok"]

    asyncio.run(scenario())


def test_cancel_all_stops_running_jobs() -> None:
    async def scenario() -> None:
        runner = scheduler_jobs.ScheduledJobRunner(max_concurrency=2, tenant_concurrency=2)
        recorder = _Recorder()
        never = asyncio.Event()
        runner.submit("a", "acme", recorder.job("acme", "a", never))
        await _settle()
        await runner.cancel_all()
        assert not runner.is_running("a")
        assert recorder.running == {"acme": 0}
        assert recorder.finished == []

    asyncio.run(scenario())


def test_limits_must_be_positive() -> None:
    with pytest.raises(ValueError):
        scheduler_jobs.ScheduledJobRunner(max_concurrency=0, tenant_concurrency=1)
