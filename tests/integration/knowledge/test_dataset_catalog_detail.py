"""Catalog dataset detail — snapshot history and schema/stats (P1b)."""

from __future__ import annotations

import time

from conftest import CONNECTIVITY, _request, holon_url


def _wait_for_version(jdoe_token: str, dataset_name: str, snapshot_id) -> list:
    """Cataloguing happens asynchronously via Knowledge's Kafka consumer
    — same convergence race as everywhere else in this suite.
    """
    deadline = time.monotonic() + 30
    versions: list = []
    while time.monotonic() < deadline:
        status, versions = _request(
            "GET", holon_url(f"/catalog/datasets/{dataset_name}/versions"), token=jdoe_token
        )
        assert status == 200, versions
        if any(str(v["snapshot_id"]) == str(snapshot_id) for v in versions):
            return versions
        time.sleep(1)
    raise AssertionError(f"snapshot {snapshot_id} for {dataset_name!r} never appeared in version history: {versions}")


def test_dataset_versions_lists_full_snapshot_history(jdoe_token: str) -> None:
    """`dataset_version` has always recorded one row per sync — this
    just exposes it. Two syncs must show as two distinct versions,
    newest first.
    """
    status, first_sync = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "customers"})
    assert status == 200, first_sync

    status, second_sync = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "customers"})
    assert status == 200, second_sync
    assert second_sync["snapshot_id"] != first_sync["snapshot_id"], second_sync

    versions = _wait_for_version(jdoe_token, "customers", second_sync["snapshot_id"])
    snapshot_ids = {str(v["snapshot_id"]) for v in versions}
    assert str(first_sync["snapshot_id"]) in snapshot_ids, versions
    assert str(second_sync["snapshot_id"]) in snapshot_ids, versions
    # Newest first.
    assert str(versions[0]["snapshot_id"]) == str(second_sync["snapshot_id"]), versions


def test_dataset_versions_for_an_unsynced_dataset_is_empty(jdoe_token: str) -> None:
    status, versions = _request(
        "GET", holon_url("/catalog/datasets/never-synced-dataset-xyz/versions"), token=jdoe_token
    )
    assert status == 200, versions
    assert versions == [], versions


def test_dataset_stats_reports_real_schema_and_column_stats(jdoe_token: str) -> None:
    status, sync_result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "customers"})
    assert status == 200, sync_result

    status, stats = _request("GET", holon_url("/catalog/datasets/customers/stats"), token=jdoe_token)
    assert status == 200, stats
    assert stats["row_count"] > 0, stats

    by_name = {c["name"]: c for c in stats["columns"]}
    assert "id" in by_name, stats
    id_col = by_name["id"]
    assert id_col["type"] in ("long", "int"), id_col
    assert id_col["null_count"] == 0, id_col
    assert id_col["distinct_count"] == stats["row_count"], id_col
    assert id_col["min"] is not None and id_col["max"] is not None, id_col

    assert "email" in by_name, stats
    assert by_name["email"]["type"] == "string", by_name["email"]


def test_dataset_stats_for_an_unsynced_dataset_is_404(jdoe_token: str) -> None:
    status, body = _request("GET", holon_url("/catalog/datasets/never-synced-dataset-xyz/stats"), token=jdoe_token)
    assert status == 404, body
