"""Tests for Query Log."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

import pytest
from conftest import IDENTITY, KNOWLEDGE, ontology_url, holon_url


def _request(method: str, url: str, *, token: str | None = None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _token_for(principal_urn: str) -> str:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        local_name = principal_urn.rsplit(":", 1)[-1]
        data = json.dumps({"principal_urn": principal_urn, "client_secret": f"{local_name}-dev-secret"}).encode()
        req = urllib.request.Request(f"{IDENTITY}/token", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read())["access_token"]
        except urllib.error.HTTPError:
            time.sleep(1.5)
    pytest.fail(f"could not mint a token for {principal_urn}")


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
