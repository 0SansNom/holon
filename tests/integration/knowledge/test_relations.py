"""Tests for Relations."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import CONNECTIVITY, IDENTITY, KNOWLEDGE, TENANT_ID, _request, ontology_url, holon_url, as_items


WORKSPACE_ID = "main"

CUSTOMER_WITH_ORDERS = 1
EXPECTED_ORDER_COUNT_FOR_CUSTOMER_WITH_ORDERS = 3
CUSTOMER_WITHOUT_ORDERS = 3


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
def orders_synced(jdoe_token: str) -> dict:
    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "orders"})
    assert status == 200, result
    assert result["dataset_urn"] == f"hl:{TENANT_ID}:{WORKSPACE_ID}:dataset:orders"
    return result


def test_orders_dataset_is_catalogued_with_column_lineage(jdoe_token: str, orders_synced: dict) -> None:
    order_object_type_urn = f"hl:{TENANT_ID}:{WORKSPACE_ID}:object-type:Order"

    deadline = time.monotonic() + 30
    edges: list = []
    while time.monotonic() < deadline:
        status, edges = _request(
            "GET", holon_url(f"/lineage/{orders_synced['dataset_version_urn']}"), token=jdoe_token
        )
        assert status == 200, edges
        column_edges = [e for e in edges if e["source_column"]]
        if len(column_edges) == 6:  # id, customerId, product, amount, status, orderedAt
            break
        time.sleep(1)

    assert any(
        e["target_urn"] == order_object_type_urn and e["relation"] == "maps_to" and not e["source_column"]
        for e in edges
    ), edges


def test_order_object_type_classification_is_confidential(jdoe_token: str, orders_synced: dict) -> None:
    status, object_type = _request("GET", ontology_url("/objectTypes/Order"), token=jdoe_token)
    assert status == 200, object_type
    # `amount` is the only confidential column in the mapping
    assert object_type["classification"] == "confidential", object_type


def test_relation_traversal_returns_the_right_orders(jdoe_token: str, orders_synced: dict) -> None:
    status, body = _request(
        "GET", ontology_url(f"/objects/Customer/{CUSTOMER_WITH_ORDERS}/links/orders"), token=jdoe_token, unwrap_pages=False
    )
    assert status == 200, body
    orders = as_items(body)
    assert len(orders) == EXPECTED_ORDER_COUNT_FOR_CUSTOMER_WITH_ORDERS
    assert all(order["customer_id"] == CUSTOMER_WITH_ORDERS for order in orders)


def test_relation_traversal_for_customer_without_orders_is_empty(jdoe_token: str, orders_synced: dict) -> None:
    status, body = _request(
        "GET", ontology_url(f"/objects/Customer/{CUSTOMER_WITHOUT_ORDERS}/links/orders"), token=jdoe_token, unwrap_pages=False
    )
    assert status == 200, body
    assert as_items(body) == []


def test_relation_traversal_goes_through_the_same_pdp(alice_token: str, orders_synced: dict) -> None:
    status, body = _request(
        "GET", ontology_url(f"/objects/Customer/{CUSTOMER_WITH_ORDERS}/links/orders"), token=alice_token
    )
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body


def test_order_objects_are_directly_resolvable(jdoe_token: str, orders_synced: dict) -> None:
    status, orders = _request("GET", ontology_url("/objects/Order"), token=jdoe_token)
    assert status == 200
    assert len(orders) >= EXPECTED_ORDER_COUNT_FOR_CUSTOMER_WITH_ORDERS

    first_id = orders[0]["id"]
    status, order = _request("GET", ontology_url(f"/objects/Order/{first_id}"), token=jdoe_token)
    assert status == 200
    assert order["id"] == first_id

    status, body = _request("GET", ontology_url("/objects/Order/999999"), token=jdoe_token)
    assert status == 404, body
