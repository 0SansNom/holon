"""End-to-end verification of the fourth connector — file import (§9.1,
§3's architecture diagram names "import fichiers" as a distinct
Connectivity capability) — and the standalone `Supplier` ObjectType it
feeds. Black-box over HTTP, same style as the other connector tests.
Requires the stack running (`make up`).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

IDENTITY = "http://localhost:8001"
CONNECTIVITY = "http://localhost:8002"
KNOWLEDGE = "http://localhost:8003"

TENANT_ID = "acme"
WORKSPACE_ID = "demo"

# Mirrors docker/csv-seed/suppliers.csv.
SEEDED_SUPPLIER_COUNT = 10


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
def suppliers_synced(jdoe_token: str) -> dict:
    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "suppliers"})
    assert status == 200, result
    assert result["dataset_urn"] == f"hl:{TENANT_ID}:{WORKSPACE_ID}:dataset:suppliers"
    assert result["row_count"] == SEEDED_SUPPLIER_COUNT
    return result


def test_file_connector_is_a_fourth_distinct_connector(jdoe_token: str, suppliers_synced: dict) -> None:
    status, syncs = _request("GET", f"{CONNECTIVITY}/syncs", token=jdoe_token)
    assert status == 200

    matching = [s for s in syncs if s["dataset_version_urn"] == suppliers_synced["dataset_version_urn"]]
    assert len(matching) == 1, syncs
    assert matching[0]["connector_urn"] == f"hl:{TENANT_ID}:global:connector:csv-landing-suppliers"


def test_supplier_classification_is_internal(jdoe_token: str, suppliers_synced: dict) -> None:
    # Recomputed asynchronously by the catalog consumer (R1.5) — poll
    # rather than assume convergence, same race every other connector
    # test already handles.
    deadline = time.monotonic() + 30
    object_type: dict = {}
    while time.monotonic() < deadline:
        status, object_type = _request("GET", f"{KNOWLEDGE}/ontology/Supplier", token=jdoe_token)
        assert status == 200, object_type
        if object_type["classification"] == "internal":
            break
        time.sleep(1)
    assert object_type["classification"] == "internal", object_type


def test_suppliers_are_directly_resolvable(jdoe_token: str, suppliers_synced: dict) -> None:
    deadline = time.monotonic() + 30
    suppliers: list = []
    while time.monotonic() < deadline:
        status, suppliers = _request("GET", f"{KNOWLEDGE}/objects/Supplier", token=jdoe_token)
        assert status == 200, suppliers
        if len(suppliers) == SEEDED_SUPPLIER_COUNT:
            break
        time.sleep(1)
    assert len(suppliers) == SEEDED_SUPPLIER_COUNT, suppliers

    first_id = suppliers[0]["id"]
    status, supplier = _request("GET", f"{KNOWLEDGE}/objects/Supplier/{first_id}", token=jdoe_token)
    assert status == 200
    assert supplier["id"] == first_id

    status, body = _request("GET", f"{KNOWLEDGE}/objects/Supplier/999999", token=jdoe_token)
    assert status == 404, body
