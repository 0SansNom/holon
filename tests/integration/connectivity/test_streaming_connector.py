"""Tests for Streaming Connector."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import IDENTITY, KNOWLEDGE, _request, ontology_url, holon_url


# Mirrors docker/stream-seed/inventory_events.jsonl
# with SKU-1001 and SKU-1004 updated more than once (latest-wins).
EXPECTED_SKU_COUNT = 8
EXPECTED_LATEST_QUANTITY = {
    "SKU-1001": 115,
    "SKU-1004": 58,
    "SKU-1002": 45,
}


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
def inventory_levels(jdoe_token: str) -> list[dict]:
    # The streaming task's micro-batch interval is 5s (see
    # `stream_connector.BATCH_INTERVAL_SECONDS`) plus catalog/materialization
    # convergence on top
    # this dataset by design.
    deadline = time.monotonic() + 60
    rows: list[dict] = []
    while time.monotonic() < deadline:
        status, rows = _request("GET", ontology_url("/objects/InventoryLevel"), token=jdoe_token)
        assert status == 200, rows
        if len(rows) == EXPECTED_SKU_COUNT:
            break
        time.sleep(2)
    return rows


def test_streaming_connector_populates_with_zero_manual_sync(inventory_levels: list[dict]) -> None:
    assert len(inventory_levels) == EXPECTED_SKU_COUNT, inventory_levels


def test_streaming_connector_reflects_latest_reading_per_sku(inventory_levels: list[dict]) -> None:
    by_id = {row["id"]: row for row in inventory_levels}
    for sku, expected_quantity in EXPECTED_LATEST_QUANTITY.items():
        assert sku in by_id, (sku, by_id)
        assert by_id[sku]["quantity"] == expected_quantity, (sku, by_id[sku])


def test_inventory_level_is_directly_resolvable(jdoe_token: str, inventory_levels: list[dict]) -> None:
    status, row = _request("GET", ontology_url("/objects/InventoryLevel/SKU-1001"), token=jdoe_token)
    assert status == 200, row
    assert row["id"] == "SKU-1001"
    assert row["quantity"] == EXPECTED_LATEST_QUANTITY["SKU-1001"]

    status, body = _request("GET", ontology_url("/objects/InventoryLevel/SKU-9999"), token=jdoe_token)
    assert status == 404, body


def test_inventory_classification_is_internal(jdoe_token: str, inventory_levels: list[dict]) -> None:
    deadline = time.monotonic() + 30
    object_type: dict = {}
    while time.monotonic() < deadline:
        status, object_type = _request("GET", ontology_url("/objectTypes/InventoryLevel"), token=jdoe_token)
        assert status == 200, object_type
        if object_type["classification"] == "internal":
            break
        time.sleep(1)
    assert object_type["classification"] == "internal", object_type
