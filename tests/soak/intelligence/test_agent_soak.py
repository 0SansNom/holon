"""Intelligence soak / light chaos (fake LLM).

Marked ``soak`` — excluded from default ``make test`` / PR e2e.
Nightly (or ``pytest -m soak``) runs concurrent sessions, multi-turn
reuse, and brief pause resilience.
"""

from __future__ import annotations

import concurrent.futures
import time
import uuid

import pytest
from conftest import INTELLIGENCE, _request

pytestmark = pytest.mark.soak


def _new_session(token: str) -> str:
    status, body = _request("POST", f"{INTELLIGENCE}/sessions", token=token)
    assert status == 200, body
    return body["urn"]


def test_soak_concurrent_sessions_complete(jdoe_token: str) -> None:
    """Open many sessions and run one turn each in parallel (fake LLM)."""
    n = 8

    def _one(_: int) -> dict:
        urn = _new_session(jdoe_token)
        status, turn = _request(
            "POST",
            f"{INTELLIGENCE}/sessions/{urn}/turns",
            token=jdoe_token,
            body={"message": f"soak ping {uuid.uuid4().hex[:8]}"},
        )
        assert status == 200, turn
        assert turn.get("sessionStatus") == "running", turn
        assert turn["text"], turn
        return turn

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_one, range(n)))
    assert len(results) == n
    assert all(r["consumed"]["iterations"] >= 1 for r in results)


def test_soak_multi_turn_session_stays_running(jdoe_token: str) -> None:
    urn = _new_session(jdoe_token)
    for i in range(5):
        status, turn = _request(
            "POST",
            f"{INTELLIGENCE}/sessions/{urn}/turns",
            token=jdoe_token,
            body={"message": f"turn {i}"},
        )
        assert status == 200, turn
        assert turn.get("sessionStatus") == "running", turn
        assert turn["sessionUrn"] == urn, turn

    status, fetched = _request("GET", f"{INTELLIGENCE}/sessions/{urn}", token=jdoe_token)
    assert status == 200, fetched
    assert fetched["status"] == "running", fetched
    assert fetched["consumed"]["iterations"] >= 5, fetched


def test_chaos_second_turn_after_brief_pause(jdoe_token: str) -> None:
    """Session survives a pause (TTL sliding) and accepts another turn."""
    urn = _new_session(jdoe_token)
    status, turn1 = _request(
        "POST",
        f"{INTELLIGENCE}/sessions/{urn}/turns",
        token=jdoe_token,
        body={"message": "before pause"},
    )
    assert status == 200, turn1
    time.sleep(2)
    status, turn2 = _request(
        "POST",
        f"{INTELLIGENCE}/sessions/{urn}/turns",
        token=jdoe_token,
        body={"message": "after pause"},
    )
    assert status == 200, turn2
    assert turn2["sessionUrn"] == urn
    assert turn2.get("sessionStatus") == "running"
