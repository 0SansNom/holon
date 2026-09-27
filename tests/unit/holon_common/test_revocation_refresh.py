"""A failed snapshot refresh must not wipe the in-memory denylist."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "libs"))

from holon_common.auth import is_jti_revoked, mark_jti_revoked, reset_revocation_state  # noqa: E402
from holon_common import principal_status  # noqa: E402


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_revocation_state()
    yield
    reset_revocation_state()


def test_failed_refresh_keeps_revoked_jtis(monkeypatch: pytest.MonkeyPatch) -> None:
    mark_jti_revoked("keep-me")

    async def boom(*, identity_url: str | None = None) -> None:
        raise RuntimeError("identity down")

    monkeypatch.setattr(principal_status, "hydrate_revocation_snapshot", boom)

    async def run() -> None:
        task = asyncio.create_task(principal_status.refresh_revocation_snapshot_forever(interval_seconds=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert is_jti_revoked("keep-me")
