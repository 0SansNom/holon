"""R8.2 — decision cache with event-driven invalidation
(`libs/holon_common/authz.py`). Proves the cache mechanism itself (not
just its side effects, which `test_permission_revocation.py`/
`test_agent_delegation.py` already re-verify after this feature landed):
repeated identical authorization checks are served from memory, not a
fresh SpiceDB+OPA round trip each time.

Doesn't assert exact global counter values (the `/metrics` counters are
process-wide and shared across this whole test session's concurrent
activity — asserting precise deltas would be racy); instead asserts the
*ratio* holds for a tight burst of identical reads, which is robust
regardless of what else is happening in the shared dev stack.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

import pytest

IDENTITY = "http://localhost:8001"
KNOWLEDGE = "http://localhost:8003"

TENANT_ID = "acme"


def _request(method: str, url: str, *, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


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


@pytest.fixture(scope="session")
def jdoe_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:jdoe")


def _read_cache_counters() -> tuple[int, int]:
    with urllib.request.urlopen(f"{KNOWLEDGE}/metrics", timeout=10) as response:
        text = response.read().decode()
    hits = int(re.search(r"^holon_authz_decision_cache_hits_total (\S+)$", text, re.MULTILINE).group(1).split(".")[0])
    misses = int(re.search(r"^holon_authz_decision_cache_misses_total (\S+)$", text, re.MULTILINE).group(1).split(".")[0])
    return hits, misses


def test_repeated_identical_reads_are_mostly_cache_hits(jdoe_token: str) -> None:
    hits_before, misses_before = _read_cache_counters()

    for _ in range(6):
        status, _ = _request("GET", f"{KNOWLEDGE}/objects/Order", token=jdoe_token)
        assert status == 200

    hits_after, misses_after = _read_cache_counters()

    new_hits = hits_after - hits_before
    new_misses = misses_after - misses_before
    # At most one of the six identical checks should have been a genuine
    # SpiceDB/OPA round trip (the first, unless another test's request in
    # the last 5s already warmed this exact key) — the rest must be
    # served from the in-process cache.
    assert new_misses <= 1, (new_hits, new_misses)
    assert new_hits >= 5, (new_hits, new_misses)


def test_metrics_expose_both_cache_counters(jdoe_token: str) -> None:
    status, _ = _request("GET", f"{KNOWLEDGE}/objects/Order", token=jdoe_token)
    assert status == 200
    hits, misses = _read_cache_counters()
    assert hits > 0, "expected at least one cache hit across the whole test session by this point"
    assert misses > 0, "expected at least one cache miss across the whole test session by this point"
