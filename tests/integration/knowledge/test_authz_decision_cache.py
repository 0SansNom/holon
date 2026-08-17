"""Tests for Authz Decision Cache."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

import pytest
from conftest import IDENTITY, KNOWLEDGE, _request, ontology_url, holon_url


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


def _read_cache_counters() -> tuple[int, int]:
    with urllib.request.urlopen(f"{KNOWLEDGE}/metrics", timeout=10) as response:
        text = response.read().decode()
    hits = int(re.search(r"^holon_authz_decision_cache_hits_total (\S+)$", text, re.MULTILINE).group(1).split(".")[0])
    misses = int(re.search(r"^holon_authz_decision_cache_misses_total (\S+)$", text, re.MULTILINE).group(1).split(".")[0])
    return hits, misses


def test_repeated_identical_reads_are_mostly_cache_hits(jdoe_token: str) -> None:
    hits_before, misses_before = _read_cache_counters()

    for _ in range(6):
        status, _ = _request("GET", ontology_url("/objects/Order"), token=jdoe_token)
        assert status == 200

    hits_after, misses_after = _read_cache_counters()

    new_hits = hits_after - hits_before
    new_misses = misses_after - misses_before
    # At most one of the six identical checks should have been a genuine
    # SpiceDB/OPA round trip (the first, unless another test's request in
    # the last 5s already warmed this exact key)
    # served from the in-process cache.
    assert new_misses <= 1, (new_hits, new_misses)
    assert new_hits >= 5, (new_hits, new_misses)


def test_metrics_expose_both_cache_counters(jdoe_token: str) -> None:
    status, _ = _request("GET", ontology_url("/objects/Order"), token=jdoe_token)
    assert status == 200
    hits, misses = _read_cache_counters()
    assert hits > 0, "expected at least one cache hit across the whole test session by this point"
    assert misses > 0, "expected at least one cache miss across the whole test session by this point"
