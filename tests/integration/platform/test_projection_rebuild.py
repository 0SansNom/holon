"""Tests for Projection Rebuild."""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from pathlib import Path
from conftest import TENANT_ID

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "libs"))

from holon_common import EventConsumer, create_pool

KAFKA_BOOTSTRAP = "localhost:19092"  # the OUTSIDE listener — see docker-compose.yml's redpanda service
DB_URL = f"postgresql://holon:{os.environ.get('POSTGRES_PASSWORD', 'holon12345')}@localhost:5432/holon_knowledge"


async def _replay_catalog_from_origin() -> dict[str, dict]:
    consumer = EventConsumer(
        KAFKA_BOOTSTRAP, topics=["connectivity"], group_id=f"rebuild-test-{uuid.uuid4().hex}"
    )
    await consumer.start()
    replayed: dict[str, dict] = {}
    try:
        iterator = consumer.__aiter__()
        while True:
            try:
                event = await asyncio.wait_for(iterator.__anext__(), timeout=5.0)
            except (asyncio.TimeoutError, StopAsyncIteration):
                break  # caught up to the end of the topic
            if event.event_type == "connectivity.sync.completed" and event.tenant_id == TENANT_ID:
                payload = event.payload
                # Last one wins
                # Kafka's in-order delivery makes "last seen" == "latest".
                replayed[payload["dataset_urn"]] = {
                    "snapshot_id": payload["snapshot_id"],
                    "row_count": payload["row_count"],
                    "dataset_version_urn": payload["dataset_version_urn"],
                    "location": payload["location"],
                }
    finally:
        await consumer.stop()
    return replayed


async def _live_catalog_state() -> dict[str, dict]:
    pool = await create_pool(DB_URL)
    try:
        rows = await pool.fetch(
            """
            SELECT d.urn AS dataset_urn, v.urn AS dataset_version_urn, v.snapshot_id, v.row_count, v.location
            FROM dataset d
            JOIN LATERAL (
                SELECT * FROM dataset_version dv
                WHERE dv.dataset_urn = d.urn ORDER BY dv.created_at DESC LIMIT 1
            ) v ON true
            WHERE d.tenant_id = $1
            """,
            TENANT_ID,
        )
    finally:
        await pool.close()
    return {
        row["dataset_urn"]: {
            "snapshot_id": row["snapshot_id"],
            "row_count": row["row_count"],
            "dataset_version_urn": row["dataset_version_urn"],
            "location": row["location"],
        }
        for row in rows
    }


async def _rebuild_and_compare() -> tuple[dict, dict]:
    replayed, live = await asyncio.gather(_replay_catalog_from_origin(), _live_catalog_state())
    return replayed, live


def test_catalog_projection_is_reconstructible_from_the_bus_alone() -> None:
    # A test running immediately before this one in the suite (e.g.
    # test_file_connector.py) may have just triggered a `/sync` whose
    # event hasn't been consumed/catalogued by Knowledge's own
    # long-running consumer yet
    # replay would see it), but the live Postgres table hasn't converged.
    # That's an ordinary convergence race (other convergence-sensitive
    # tests in this suite poll for it), not a real state mismatch
    # retry the whole compare a few times before failing.
    deadline = time.monotonic() + 30
    replayed, live = {}, {}
    while time.monotonic() < deadline:
        replayed, live = asyncio.run(_rebuild_and_compare())
        if live and set(replayed) >= set(live) and all(replayed.get(urn) == state for urn, state in live.items()):
            break
        time.sleep(2)

    assert live, "no datasets catalogued yet — run a /sync first (provision-test-fixtures + seed)"
    assert set(replayed) >= set(live), (
        f"the live catalog has datasets the event log doesn't — a real state mismatch: "
        f"missing from replay: {set(live) - set(replayed)}"
    )
    for dataset_urn, live_state in live.items():
        assert replayed[dataset_urn] == live_state, (
            dataset_urn, replayed[dataset_urn], live_state
        )
