"""Tests for Link Write."""

from __future__ import annotations

import time

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


@pytest.fixture(scope="module")
def editor_token(jdoe_token: str) -> str:
    return jdoe_token


def test_fk_link_write_and_unlink_round_trip(editor_token: str) -> None:
    status, orders = _request("GET", ontology_url("/objects/Order"), token=editor_token)
    assert status == 200, orders
    assert orders, "expected seeded orders"
    order = orders[0]
    order_id = order["id"]
    # Overlay may leave customerId=null after a prior unlink; fall back to source col.
    original_customer = order.get("customerId")
    if original_customer is None:
        original_customer = order.get("customer_id")
    assert original_customer is not None, order

    # Point the FK overlay at customer 3 (seed has customers 1 and 3).
    new_customer = 3 if int(original_customer) != 3 else 1
    status, linked = _request(
        "PUT",
        ontology_url(f"/objects/Order/{order_id}/links/customer"),
        token=editor_token,
        body={"target_id": new_customer},
    )
    assert status == 200, linked
    assert linked["relation"] == "Order.customer", linked
    assert any(int(item["id"]) == int(new_customer) for item in linked["data"]), linked

    status, again = _request(
        "GET", ontology_url(f"/objects/Order/{order_id}/links/customer"), token=editor_token
    )
    assert status == 200, again
    assert any(int(item["id"]) == int(new_customer) for item in again["data"]), again

    status, unlinked = _request(
        "DELETE",
        ontology_url(f"/objects/Order/{order_id}/links/customer"),
        token=editor_token,
    )
    assert status == 200, unlinked
    assert unlinked["data"] == [], unlinked

    # Restore original FK so later suites keep seeing seeded graph shape.
    status, restored = _request(
        "PUT",
        ontology_url(f"/objects/Order/{order_id}/links/customer"),
        token=editor_token,
        body={"target_id": original_customer},
    )
    assert status == 200, restored
    assert any(int(item["id"]) == int(original_customer) for item in restored["data"]), restored


def test_link_write_from_reverse_side_is_rejected(editor_token: str) -> None:
    status, body = _request(
        "PUT",
        ontology_url("/objects/Customer/1/links/orders"),
        token=editor_token,
        body={"target_id": 1},
    )
    assert status == 400, body
    assert "source" in body["detail"].lower(), body


def test_link_write_requires_target_id(editor_token: str) -> None:
    status, orders = _request("GET", ontology_url("/objects/Order"), token=editor_token)
    assert status == 200 and orders
    order_id = orders[0]["id"]
    status, body = _request(
        "PUT",
        ontology_url(f"/objects/Order/{order_id}/links/customer"),
        token=editor_token,
        body={},
    )
    assert status == 400, body
