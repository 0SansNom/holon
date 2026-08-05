"""End-to-end verification of streaming ingestion and the `InventoryLevel` ObjectType it feeds.

Unlike every other connector test, this one never calls `/sync`: the
background task in `stream_connector.py` is already continuously
consuming `external-inventory-stream` (seeded once at stack startup by
the `inventory-stream-seed` one-shot container) and committing snapshots
on its own. Proving the data shows up with *zero* manual trigger is the
actual point of this test. Black-box over HTTP, same style as the other
connector tests. Requires the stack running (`make up`).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

IDENTITY = "http://localhost:8001"
KNOWLEDGE = "http://localhost:8003"

TENANT_ID = "acme"

# Mirrors docker/stream-seed/inventory_events.jsonl — 8 distinct SKUs,
# with SKU-1001 and SKU-1004 updated more than once (latest-wins).
EXPECTED_SKU_COUNT = 8
EXPECTED_LATEST_QUANTITY = {
    "SKU-1001": 115,
    "SKU-1004": 58,
    "SKU-1002": 45,
}


def _request(method: str, url: str, *, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
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


@pytest.fixture(scope="session")
def inventory_levels(jdoe_token: str) -> list[dict]:
    # The streaming task's micro-batch interval is 5s (see
    # `stream_connector.BATCH_INTERVAL_SECONDS`) plus catalog/materialization
    # convergence on top — poll generously, no manual /sync exists for
    # this dataset by design.
    deadline = time.monotonic() + 60
    rows: list[dict] = []
    while time.monotonic() < deadline:
        status, rows = _request("GET", f"{KNOWLEDGE}/objects/InventoryLevel", token=jdoe_token)
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
    status, row = _request("GET", f"{KNOWLEDGE}/objects/InventoryLevel/SKU-1001", token=jdoe_token)
    assert status == 200, row
    assert row["id"] == "SKU-1001"
    assert row["quantity"] == EXPECTED_LATEST_QUANTITY["SKU-1001"]

    status, body = _request("GET", f"{KNOWLEDGE}/objects/InventoryLevel/SKU-9999", token=jdoe_token)
    assert status == 404, body


def test_inventory_classification_is_internal(jdoe_token: str, inventory_levels: list[dict]) -> None:
    deadline = time.monotonic() + 30
    object_type: dict = {}
    while time.monotonic() < deadline:
        status, object_type = _request("GET", f"{KNOWLEDGE}/ontology/InventoryLevel", token=jdoe_token)
        assert status == 200, object_type
        if object_type["classification"] == "internal":
            break
        time.sleep(1)
    assert object_type["classification"] == "internal", object_type
