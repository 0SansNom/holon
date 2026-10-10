"""Bounded, per-tenant fair execution of scheduled jobs."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Hashable

logger = logging.getLogger("connectivity.scheduler")


class ScheduledJobRunner:
    """Runs scheduled jobs in the background without letting one tenant starve the others.

    Each job holds a slot of its tenant's semaphore, then a slot of the global one, so
    a tenant at its cap queues behind itself and never occupies a global slot. A job
    still running (or queued) under the same key is not started twice.
    """

    def __init__(self, *, max_concurrency: int, tenant_concurrency: int) -> None:
        if max_concurrency < 1 or tenant_concurrency < 1:
            raise ValueError("scheduler concurrency limits must be at least 1")
        self._global = asyncio.Semaphore(max_concurrency)
        self._tenant_concurrency = tenant_concurrency
        self._tenants: dict[str, asyncio.Semaphore] = {}
        self._jobs: dict[Hashable, asyncio.Task] = {}

    def is_running(self, key: Hashable) -> bool:
        task = self._jobs.get(key)
        return task is not None and not task.done()

    def submit(self, key: Hashable, tenant_id: str, job: Callable[[], Awaitable[None]]) -> bool:
        if self.is_running(key):
            return False
        self._jobs[key] = asyncio.create_task(self._run(key, tenant_id, job))
        return True

    async def _run(self, key: Hashable, tenant_id: str, job: Callable[[], Awaitable[None]]) -> None:
        tenant = self._tenants.setdefault(tenant_id, asyncio.Semaphore(self._tenant_concurrency))
        async with tenant, self._global:
            try:
                await job()
            except Exception:
                logger.exception("scheduled job %r failed (tenant=%s) — will retry next poll", key, tenant_id)

    def reap(self) -> None:
        for key in [key for key, task in self._jobs.items() if task.done()]:
            del self._jobs[key]

    async def cancel_all(self) -> None:
        tasks = list(self._jobs.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._jobs.clear()
