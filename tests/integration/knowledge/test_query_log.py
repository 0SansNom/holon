"""Tests for Query Log."""

from __future__ import annotations

import time
import uuid

from conftest import _request, holon_url


def test_search_query_appears_in_the_query_log(jdoe_token: str) -> None:
    marker = f"probe-{uuid.uuid4().hex}"
    status, search_result = _request("GET", holon_url(f"/search?q={marker}"), token=jdoe_token)
    assert status == 200, search_result

    deadline = time.monotonic() + 15
    entry = None
    while time.monotonic() < deadline:
        status, log = _request("GET", holon_url("/query-log"), token=jdoe_token)
        assert status == 200, log
        entry = next((e for e in log if e["query_text"] == marker), None)
        if entry is not None:
            break
        time.sleep(1)

    assert entry is not None, "query never appeared in /query-log"
    assert entry["result_count"] == search_result["total"], (entry, search_result)


def test_query_log_entries_carry_no_identifying_field(jdoe_token: str) -> None:
    status, log = _request("GET", holon_url("/query-log"), token=jdoe_token)
    assert status == 200, log
    assert log, "query log is empty — run the other test first or issue a /search"
    identifying_keys = {"principal_urn", "principal", "actor", "user", "user_urn", "urn"}
    for entry in log:
        assert not (set(entry.keys()) & identifying_keys), entry
