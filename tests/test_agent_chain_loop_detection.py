"""Loop detection when an event spawns a new agent session
(`services/automation/app/agent_chain_trigger.py`). Proves the
causation-depth circuit breaker actually stops a chain rather than
letting it run forever: a session opts into `chain_trigger` with a low
`max_chain_depth` (test-overridable, same idiom as `actions.py`'s
`ttl_seconds` override — keeps this to a handful of real LLM calls
instead of ten), and the chain must terminate at exactly that depth, not
before and not after.

White-box on Intelligence's own `agent_session` table (same technique
already proven in `test_dlq.py`/`test_projection_rebuild.py`) — the
chain-triggered sessions belong to Automation's own service-account
principal, which this test's own token can't read back over the API
(a session belongs to the agent that created it). Requires the
stack running (`make up`), real LLM calls (2 chain hops + the root turn
= 3 total).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from conftest import IDENTITY, INTELLIGENCE, TENANT_ID, _request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libs"))

from holon_common import create_pool  # noqa: E402

# Real, metered Anthropic calls — excluded from CI by default (cost +
# secret-exposure risk); run explicitly with `pytest -m llm`.
pytestmark = pytest.mark.llm

# Password read from the environment, not hardcoded — CI generates its
# own .env with a different POSTGRES_PASSWORD than a dev's local one
# (see .github/workflows/tests.yml). Default matches .env.example's dev
# convenience value for a plain `pytest tests/` run against `make up`.
INTELLIGENCE_DB_URL = f"postgresql://holon:{os.environ.get('POSTGRES_PASSWORD', 'holon12345')}@localhost:5432/holon_intelligence"

CHAIN_TRIGGER_AGENT_URN = f"hl:{TENANT_ID}:global:service-account:automation-agent-chain-trigger"
MAX_CHAIN_DEPTH = 2  # keeps this test to 3 real LLM calls total (root + 2 hops), not 11


def _token_for(principal_urn: str) -> str:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        local_name = principal_urn.rsplit(":", 1)[-1]
        status, body = _request(
            "POST",
            f"{IDENTITY}/token",
            body={"principal_urn": principal_urn, "client_secret": f"{local_name}-dev-secret"},
        )
        if status == 200:
            return body["access_token"]
        time.sleep(1.5)
    pytest.fail(f"could not mint a token for {principal_urn}")


async def _chain_trigger_session_count(min_depth: int = 1) -> int:
    pool = await create_pool(INTELLIGENCE_DB_URL)
    try:
        return await pool.fetchval(
            "SELECT count(*) FROM agent_session WHERE agent_urn = $1 AND chain_trigger = TRUE AND causation_depth >= $2",
            CHAIN_TRIGGER_AGENT_URN,
            min_depth,
        )
    finally:
        await pool.close()


def _poll_chain_trigger_session_count(*, expect_at_least: int, timeout: float = 90.0) -> int:
    deadline = time.monotonic() + timeout
    count = 0
    while time.monotonic() < deadline:
        count = asyncio.run(_chain_trigger_session_count())
        if count >= expect_at_least:
            return count
        time.sleep(2)
    return count


async def _clear_chain_trigger_sessions() -> None:
    """Test hygiene, not part of what's being proven: a prior failed run
    (or this test re-run) can leave chain-triggered sessions behind, and
    the count this test asserts on has no way to scope to just its own
    chain (only a causation_id pointer to the parent event, not a root
    session urn) — so start from a clean slate instead.
    """
    pool = await create_pool(INTELLIGENCE_DB_URL)
    try:
        # agent_turn.session_urn REFERENCES agent_session(urn), no cascade
        # — children first.
        await pool.execute(
            "DELETE FROM agent_turn WHERE session_urn IN "
            "(SELECT urn FROM agent_session WHERE agent_urn = $1 AND chain_trigger = TRUE)",
            CHAIN_TRIGGER_AGENT_URN,
        )
        await pool.execute(
            "DELETE FROM agent_session WHERE agent_urn = $1 AND chain_trigger = TRUE", CHAIN_TRIGGER_AGENT_URN
        )
    finally:
        await pool.close()


def test_chain_trigger_terminates_at_max_chain_depth_not_before_or_after(jdoe_token: str) -> None:
    asyncio.run(_clear_chain_trigger_sessions())

    status, session = _request(
        "POST",
        f"{INTELLIGENCE}/sessions",
        token=jdoe_token,
        body={"causation_depth": 0, "chain_trigger": True, "max_chain_depth": MAX_CHAIN_DEPTH},
    )
    assert status == 200, session
    assert session["chain_trigger"] is True, session
    assert session["max_chain_depth"] == MAX_CHAIN_DEPTH, session

    status, turn = _request(
        "POST",
        f"{INTELLIGENCE}/sessions/{session['urn']}/turns",
        token=jdoe_token,
        body={"message": "Reply with a short acknowledgement. Do not use any tools."},
    )
    assert status == 200, turn

    # Automation's agent_chain_trigger should react to the root session's
    # completion event and spawn depth=1, which completes and spawns
    # depth=2 — exactly MAX_CHAIN_DEPTH hops, no more.
    reached = _poll_chain_trigger_session_count(expect_at_least=MAX_CHAIN_DEPTH)
    assert reached == MAX_CHAIN_DEPTH, (
        f"expected exactly {MAX_CHAIN_DEPTH} chain-triggered sessions (depths 1..{MAX_CHAIN_DEPTH}), found {reached}"
    )

    # Give a would-be depth=(MAX_CHAIN_DEPTH+1) hop time to appear if the
    # loop guard were broken, then confirm it never does.
    time.sleep(15)
    final_count = asyncio.run(_chain_trigger_session_count())
    assert final_count == MAX_CHAIN_DEPTH, (
        f"chain grew past max_chain_depth={MAX_CHAIN_DEPTH} (found {final_count} sessions) — P3.B6 loop guard failed"
    )
