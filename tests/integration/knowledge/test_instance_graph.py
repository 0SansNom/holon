"""Tests for Instance Graph."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections import Counter

import pytest
from conftest import CONNECTIVITY, IDENTITY, KNOWLEDGE, _request, ontology_url, holon_url


WORKSPACE_ID = "main"

# Mirrors seed/source_erp.sql + docker/mongo-init/init.js + docker/reviews-api/reviews.json:
# Customer 1 "Acme Robotics" -> 3 Orders, 2 SupportTickets (hop 1);
# Order 1 and Order 2 each have one ProductReview, Order 3 has none (hop 2).
CUSTOMER_WITH_FULL_NEIGHBORHOOD = 1


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
def full_neighborhood_synced(jdoe_token: str) -> None:
    """Unlike `test_relations.py`'s single-dataset fixture, this."""
    for dataset in ("customers", "orders", "support_tickets", "reviews"):
        status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": dataset})
        assert status == 200, result


def test_two_hop_neighborhood_covers_orders_tickets_and_reviews(
    jdoe_token: str, full_neighborhood_synced: None
) -> None:
    status, graph = _request(
        "GET", ontology_url(f"/objects/Customer/{CUSTOMER_WITH_FULL_NEIGHBORHOOD}/graph?hops=2"), token=jdoe_token
    )
    assert status == 200, graph
    assert graph["root"] == f"Customer:{CUSTOMER_WITH_FULL_NEIGHBORHOOD}"
    assert graph["truncated"] is False

    by_type_hop = Counter((n["objectType"], n["hop"]) for n in graph["nodes"])
    assert by_type_hop[("Customer", 0)] == 1
    assert by_type_hop[("Order", 1)] == 3
    assert by_type_hop[("SupportTicket", 1)] == 2
    assert by_type_hop[("ProductReview", 2)] == 2  # Order 3 has no review, so only 2 of 3 Orders yield one

    # `<=`, not `==`: other test/dev activity against this same stack can
    # register additional relation types that also reach Customer 1 within
    # 2 hops (e.g. a manually-created RelationType pointing at Customer)
    # this pins that the three expected relations are present, same
    # tolerant-superset style `test_seeded_relation_types_are_listed`
    # already uses, not that they're the only ones.
    relations_used = {e["relation"] for e in graph["edges"]}
    assert {"Order.customer", "SupportTicket.customer", "ProductReview.order"} <= relations_used


def test_one_hop_neighborhood_excludes_the_second_hop_reviews(
    jdoe_token: str, full_neighborhood_synced: None
) -> None:
    status, graph = _request(
        "GET", ontology_url(f"/objects/Customer/{CUSTOMER_WITH_FULL_NEIGHBORHOOD}/graph?hops=1"), token=jdoe_token
    )
    assert status == 200, graph
    # `<=`, not `==`
    object_types = {n["objectType"] for n in graph["nodes"]}
    assert {"Customer", "Order", "SupportTicket"} <= object_types
    assert "ProductReview" not in object_types, "hops=1 must not reach ProductReview (2 hops away)"


def test_hops_beyond_the_max_are_clamped_not_rejected(jdoe_token: str, full_neighborhood_synced: None) -> None:
    status_capped, graph_capped = _request(
        "GET", ontology_url(f"/objects/Customer/{CUSTOMER_WITH_FULL_NEIGHBORHOOD}/graph?hops=3"), token=jdoe_token
    )
    status_over, graph_over = _request(
        "GET", ontology_url(f"/objects/Customer/{CUSTOMER_WITH_FULL_NEIGHBORHOOD}/graph?hops=99"), token=jdoe_token
    )
    assert status_capped == 200, graph_capped
    assert status_over == 200, graph_over  # never a 422/500 for an out-of-range value — clamped, not rejected
    assert len(graph_over["nodes"]) == len(graph_capped["nodes"])


def test_graph_traversal_goes_through_the_same_pdp(alice_token: str, full_neighborhood_synced: None) -> None:
    status, body = _request(
        "GET", ontology_url(f"/objects/Customer/{CUSTOMER_WITH_FULL_NEIGHBORHOOD}/graph"), token=alice_token
    )
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body


def test_graph_for_unknown_instance_is_404(jdoe_token: str, full_neighborhood_synced: None) -> None:
    status, body = _request("GET", ontology_url("/objects/Customer/999999/graph"), token=jdoe_token)
    assert status == 404, body


def test_graph_for_unknown_object_type_is_404(jdoe_token: str) -> None:
    status, body = _request("GET", ontology_url("/objects/NotAType/1/graph"), token=jdoe_token)
    assert status == 404, body
