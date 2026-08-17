"""Tests for Serving Store."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import CONNECTIVITY, IDENTITY, KNOWLEDGE, _request, ontology_url, holon_url, as_items


WORKSPACE_ID = "main"

ORDER_WITH_CUSTOMER = 1  # Customer 1 (Acme Robotics)'s first order


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
def customers_synced(jdoe_token: str) -> dict:
    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "customers"})
    assert status == 200, result
    return result


@pytest.fixture(scope="session")
def orders_synced(jdoe_token: str) -> dict:
    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "orders"})
    assert status == 200, result
    return result


def _poll_materialized(jdoe_token: str, url: str) -> dict:
    """Materialization runs asynchronously in the catalog consumer, same."""
    deadline = time.monotonic() + 30
    body: dict = {}
    while time.monotonic() < deadline:
        status, body = _request("GET", url, token=jdoe_token)
        assert status == 200, body
        if body.get("degraded") is False:
            return body
        time.sleep(1)
    pytest.fail(f"serving store never converged for {url}: {body}")


def test_customer_read_is_materialized_with_freshness_metadata(jdoe_token: str, customers_synced: dict) -> None:
    customer = _poll_materialized(jdoe_token, ontology_url("/objects/Customer/1"))
    assert customer["degraded"] is False
    assert customer["materializedAt"] is not None
    assert isinstance(customer["sourceLagSeconds"], int)
    assert customer["sourceLagSeconds"] >= 0


def test_relation_traversal_still_resolves_through_the_serving_store(
    jdoe_token: str, customers_synced: dict, orders_synced: dict
) -> None:
    _poll_materialized(jdoe_token, ontology_url("/objects/Customer/1"))

    # The "orders" sync materializes independently of "customers"
    # the traversal itself for convergence too, same race as everywhere else.
    deadline = time.monotonic() + 30
    orders: list = []
    while time.monotonic() < deadline:
        status, body = _request(
            "GET", ontology_url("/objects/Customer/1/links/orders"), token=jdoe_token, unwrap_pages=False
        )
        assert status == 200, body
        orders = as_items(body)
        if orders and all(o["degraded"] is False for o in orders):
            break
        time.sleep(1)
    else:
        pytest.fail(f"order traversal never converged: {orders}")

    assert len(orders) >= 1
    assert all(order["customer_id"] == 1 for order in orders)
    matching = [o for o in orders if o["id"] == ORDER_WITH_CUSTOMER]
    assert len(matching) == 1, orders
    assert matching[0]["degraded"] is False


def test_credit_hold_overlay_still_applies_on_a_materialized_read(jdoe_token: str, customers_synced: dict) -> None:
    customer_id = 8  # Orion Data Systems — untouched by other test modules' Actions
    status, body = _request(
        "POST",
        ontology_url(f"/objects/Customer/{customer_id}/actions/putOnCreditHold"),
        token=jdoe_token,
        body={"reason": "serving store regression check"},
    )
    assert status == 200, body

    customer = _poll_materialized(jdoe_token, ontology_url(f"/objects/Customer/{customer_id}"))
    assert customer["credit_hold"] is True, customer
