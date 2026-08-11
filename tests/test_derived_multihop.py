"""Multi-hop link_aggregate derived properties (Foundry ≤3 hops)."""

from __future__ import annotations

import pytest
from conftest import CONNECTIVITY, KNOWLEDGE, _request


CUSTOMER_ID = 1


@pytest.fixture(scope="module")
def graph_synced(jdoe_token: str) -> None:
    for dataset in ("customers", "orders", "support_tickets", "reviews"):
        status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": dataset})
        assert status == 200, result


def test_two_hop_link_aggregate_count_and_collect(
    msmith_token: str, jdoe_token: str, graph_synced: None
) -> None:
    status, draft = _request(
        "POST",
        f"{KNOWLEDGE}/ontology/Customer/versions",
        token=msmith_token,
        body={
            "derived_properties": {
                "reviewCount": {
                    "kind": "link_aggregate",
                    "path": ["orders", "reviews"],
                    "aggregate": "count",
                },
                "orderProducts": {
                    "kind": "link_aggregate",
                    "path": ["orders"],
                    "aggregate": "collect_list",
                    "property": "product",
                    "collect_limit": 10,
                },
                "orderTotal": {
                    "kind": "link_aggregate",
                    "relation": "orders",
                    "aggregate": "sum",
                    "property": "amount",
                },
            }
        },
    )
    assert status == 201, draft
    status, published = _request(
        "POST",
        f"{KNOWLEDGE}/ontology/Customer/versions/{draft['version']}/publish",
        token=msmith_token,
    )
    assert status == 200, published
    assert published["derived_properties"]["reviewCount"]["path"] == ["orders", "reviews"]

    status, customer = _request(
        "GET", f"{KNOWLEDGE}/objects/Customer/{CUSTOMER_ID}", token=jdoe_token
    )
    assert status == 200, customer
    # Customer 1 → 3 orders → 2 reviews (Order 3 has none) — same fixture as instance graph.
    assert customer["reviewCount"] == 2, customer
    assert isinstance(customer["orderProducts"], list) and len(customer["orderProducts"]) == 3, customer
    assert customer["orderTotal"] is not None, customer


def test_publish_rejects_path_longer_than_three(msmith_token: str) -> None:
    status, draft = _request(
        "POST",
        f"{KNOWLEDGE}/ontology/Customer/versions",
        token=msmith_token,
        body={
            "derived_properties": {
                "tooDeep": {
                    "kind": "link_aggregate",
                    "path": ["orders", "reviews", "orders", "reviews"],
                    "aggregate": "count",
                }
            }
        },
    )
    assert status == 201, draft
    status, result = _request(
        "POST",
        f"{KNOWLEDGE}/ontology/Customer/versions/{draft['version']}/publish",
        token=msmith_token,
    )
    assert status == 400, result
    assert "path" in result["detail"] or "1–3" in result["detail"] or "1-3" in result["detail"], result
