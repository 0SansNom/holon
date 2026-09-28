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


def test_repeated_allows_are_not_served_from_cache(jdoe_token: str) -> None:
    """A grant always re-checks SpiceDB. Six identical reads are six misses."""
    hits_before, misses_before = _read_cache_counters()

    for _ in range(6):
        status, _ = _request("GET", ontology_url("/objects/Order"), token=jdoe_token)
        assert status == 200

    hits_after, misses_after = _read_cache_counters()
    assert misses_after - misses_before >= 6, (hits_after - hits_before, misses_after - misses_before)


def test_repeated_denials_are_cache_hits(alice_token: str) -> None:
    hits_before, misses_before = _read_cache_counters()

    for _ in range(4):
        status, _ = _request("GET", ontology_url("/objects/Order"), token=alice_token)
        assert status == 403

    hits_after, misses_after = _read_cache_counters()
    assert misses_after - misses_before <= 1, (hits_after - hits_before, misses_after - misses_before)
    assert hits_after - hits_before >= 3, (hits_after - hits_before, misses_after - misses_before)


def test_metrics_expose_both_cache_counters(jdoe_token: str, alice_token: str) -> None:
    status, _ = _request("GET", ontology_url("/objects/Order"), token=jdoe_token)
    assert status == 200
    status, _ = _request("GET", ontology_url("/objects/Order"), token=alice_token)
    assert status == 403
    status, _ = _request("GET", ontology_url("/objects/Order"), token=alice_token)
    assert status == 403
    hits, misses = _read_cache_counters()
    assert hits > 0, "expected at least one denial-cache hit"
    assert misses > 0, "expected at least one cache miss"
