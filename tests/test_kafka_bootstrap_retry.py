"""Unit tests for the Kafka bootstrap retry in holon_common.events.

Only the initial bootstrap is fatal to aiokafka — these tests pin the
retry/backoff behavior that keeps a service alive when it starts while
Redpanda is down or stalling. No running stack needed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libs"))

from aiokafka.errors import KafkaConnectionError

from holon_common.events import _start_with_retry


class _FlakyStartable:
    """Fails start() `failures` times with a bootstrap error, then succeeds."""

    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0

    async def start(self) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise KafkaConnectionError("Unable to bootstrap from [('redpanda', 9092)]")


def test_succeeds_after_transient_bootstrap_failures():
    startable = _FlakyStartable(failures=3)
    asyncio.run(_start_with_retry(startable, what="test", attempts=5, delay=0))
    assert startable.calls == 4


def test_reraises_after_exhausting_attempts():
    startable = _FlakyStartable(failures=99)
    with pytest.raises(KafkaConnectionError):
        asyncio.run(_start_with_retry(startable, what="test", attempts=3, delay=0))
    assert startable.calls == 3


def test_no_retry_on_successful_first_attempt():
    startable = _FlakyStartable(failures=0)
    asyncio.run(_start_with_retry(startable, what="test", attempts=5, delay=0))
    assert startable.calls == 1
