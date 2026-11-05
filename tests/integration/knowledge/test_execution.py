"""Tests for Execution."""

from __future__ import annotations

import time

import pytest
from conftest import CONNECTIVITY, _request as _http, holon_url


def _request(method: str, url: str, *, token: str | None = None, body: dict | None = None):
    # 60s, not 30s: Iceberg writes, DuckDB scans and serving-store
    # materialization can each approach 30s on a small host.
    return _http(method, url, token=token, body=body, timeout=60, unwrap_pages=False)


def _execute_pending_filter(jdoe_token: str) -> dict:
    status, body = _request(
        "POST",
        holon_url("/execute"),
        token=jdoe_token,
        body={"object_type": "Order", "filter_property": "status", "filter_value": "pending"},
    )
    assert status == 200, body
    return body


def test_first_execution_is_not_cached_and_returns_matching_rows(jdoe_token: str, orders_synced: dict) -> None:
    result = _execute_pending_filter(jdoe_token)
    assert result["rowCount"] >= 1, result
    assert all(row["status"] == "pending" for row in result["results"]), result


def test_repeated_execution_is_served_from_cache(jdoe_token: str, orders_synced: dict) -> None:
    first = _execute_pending_filter(jdoe_token)
    second = _execute_pending_filter(jdoe_token)
    assert second["cached"] is True, second
    assert second["planHash"] == first["planHash"], (first, second)
    assert second["rowCount"] == first["rowCount"], (first, second)
    assert second["results"] == first["results"], (first, second)


def test_a_new_sync_invalidates_the_cache_via_a_new_plan_hash(jdoe_token: str, orders_synced: dict) -> None:
    before = _execute_pending_filter(jdoe_token)

    status, resync = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "orders"})
    assert status == 200, resync

    # The catalog converges asynchronously (outbox -> bus -> consumer): the
    # new DatasetVersion is only visible once Knowledge has consumed the
    # sync event. Poll until then
    # test_walking_skeleton's test_dataset_is_catalogued
    # execute below races the consumer and can still see the old version.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status, datasets = _request("GET", holon_url("/catalog/datasets"), token=jdoe_token)
        assert status == 200
        orders = next((d for d in datasets if d["urn"] == resync["dataset_urn"]), None)
        if orders and orders["snapshot_id"] == resync["snapshot_id"]:
            break
        time.sleep(1)
    else:
        pytest.fail("catalog did not converge to the new orders snapshot in time")

    after = _execute_pending_filter(jdoe_token)
    assert after["planHash"] != before["planHash"], (before, after)
    assert after["cached"] is False, after
    assert after["rowCount"] == before["rowCount"], (before, after)  # same underlying data, just a new snapshot


def test_unknown_filter_property_is_rejected(jdoe_token: str, orders_synced: dict) -> None:
    status, body = _request(
        "POST",
        holon_url("/execute"),
        token=jdoe_token,
        body={"object_type": "Order", "filter_property": "doesNotExist", "filter_value": "x"},
    )
    assert status == 400, body
    assert "unknown property" in body["detail"], body


def test_rebac_denied_principal_cannot_execute(alice_token: str, orders_synced: dict) -> None:
    status, body = _request(
        "POST",
        holon_url("/execute"),
        token=alice_token,
        body={"object_type": "Order", "filter_property": "status", "filter_value": "pending"},
    )
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body
