"""A revoke during the snapshot replace stays revoked."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "libs"))

from holon_common.auth import (  # noqa: E402
    capture_revocation_events,
    is_jti_revoked,
    mark_jti_revoked,
    replay_revocation_events,
    replace_revoked_jtis,
    reset_revocation_state,
)
from holon_common.principal_status import principal_status_group_id  # noqa: E402
from holon_common import service_runtime  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_revocation_state()
    yield
    reset_revocation_state()


def test_revoke_during_snapshot_replace_is_kept() -> None:
    mark_jti_revoked("already")
    with capture_revocation_events() as during:
        mark_jti_revoked("fresh")
        replace_revoked_jtis(["already"])
    assert not is_jti_revoked("fresh")
    replay_revocation_events(during)
    assert is_jti_revoked("fresh")
    assert is_jti_revoked("already")


def test_group_id_is_per_host_not_per_process() -> None:
    import os

    group = principal_status_group_id("knowledge-platform")
    assert str(os.getpid()) not in group
    assert group == principal_status_group_id("knowledge-platform")


def test_listener_starts_after_hydrate(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    async def hydrate(**kwargs):
        order.append("hydrate")

    def make_consumer(*args, **kwargs):
        order.append("consumer")
        return MagicMock()

    async def idle(*args, **kwargs):
        await asyncio.sleep(3600)

    monkeypatch.setattr(service_runtime, "hydrate_revocation_snapshot", hydrate)
    monkeypatch.setattr(service_runtime, "make_principal_status_consumer", make_consumer)
    monkeypatch.setattr(service_runtime, "consume_identity_auth_events", idle)
    monkeypatch.setattr(service_runtime, "refresh_revocation_snapshot_forever", idle)

    async def run() -> None:
        handle = await service_runtime.start_principal_status(
            kafka_bootstrap="localhost:9092", service_name="knowledge-platform", authz=MagicMock(),
        )
        handle.status_task.cancel()
        handle.revocation_refresh_task.cancel()
        for task in (handle.status_task, handle.revocation_refresh_task):
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())
    assert order[:2] == ["hydrate", "consumer"]
