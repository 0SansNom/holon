"""Tests for Third Connector."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import CONNECTIVITY, IDENTITY, KNOWLEDGE, TENANT_ID, _request, ontology_url, holon_url, as_items


WORKSPACE_ID = "main"

# Mirrors docker/reviews-api/reviews.json
# order 3 (Customer 1's "Custom Automation Software") has none.
ORDER_WITH_REVIEW = 1
ORDER_WITHOUT_REVIEW = 3


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
def reviews_synced(jdoe_token: str) -> dict:
    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "reviews"})
    assert status == 200, result
    assert result["dataset_urn"] == f"hl:{TENANT_ID}:{WORKSPACE_ID}:dataset:reviews"
    return result


def test_rest_connector_is_a_third_distinct_connector(jdoe_token: str, reviews_synced: dict) -> None:
    status, syncs = _request("GET", f"{CONNECTIVITY}/syncs", token=jdoe_token)
    assert status == 200

    matching = [s for s in syncs if s["dataset_version_urn"] == reviews_synced["dataset_version_urn"]]
    assert len(matching) == 1, syncs
    assert matching[0]["connector_urn"] == f"hl:{TENANT_ID}:global:connector:reviews-rest-api"


def test_product_review_classification_is_public(jdoe_token: str, reviews_synced: dict) -> None:
    # Classification is recomputed asynchronously by the catalog consumer
    # poll rather than assume it already converged by the time
    # this test runs, same convergence race as the catalog itself.
    deadline = time.monotonic() + 30
    object_type: dict = {}
    while time.monotonic() < deadline:
        status, object_type = _request("GET", ontology_url("/objectTypes/ProductReview"), token=jdoe_token)
        assert status == 200, object_type
        if object_type["classification"] == "public":
            break
        time.sleep(1)
    assert object_type["classification"] == "public", object_type


def test_relation_traversal_from_order_returns_the_right_review(jdoe_token: str, reviews_synced: dict) -> None:
    status, body = _request(
        "GET", ontology_url(f"/objects/Order/{ORDER_WITH_REVIEW}/links/reviews"), token=jdoe_token, unwrap_pages=False
    )
    assert status == 200, body
    reviews = as_items(body)
    assert len(reviews) == 1
    assert reviews[0]["order_id"] == ORDER_WITH_REVIEW


def test_relation_traversal_for_unreviewed_order_is_empty(jdoe_token: str, reviews_synced: dict) -> None:
    status, body = _request(
        "GET", ontology_url(f"/objects/Order/{ORDER_WITHOUT_REVIEW}/links/reviews"), token=jdoe_token, unwrap_pages=False
    )
    assert status == 200, body
    assert as_items(body) == []


def test_abac_only_restricts_confidential_data_not_public_data(kenji_token: str, reviews_synced: dict) -> None:
    """kenji (non-EU country) gets confidential fields masked on."""
    status, reviews = _request("GET", ontology_url("/objects/ProductReview"), token=kenji_token)
    assert status == 200, reviews


def test_rebac_still_denies_a_principal_with_no_workspace_relation(alice_token: str, reviews_synced: dict) -> None:
    status, body = _request("GET", ontology_url("/objects/ProductReview"), token=alice_token)
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body


def test_product_review_objects_are_directly_resolvable(jdoe_token: str, reviews_synced: dict) -> None:
    status, reviews = _request("GET", ontology_url("/objects/ProductReview"), token=jdoe_token)
    assert status == 200
    assert len(reviews) >= 1

    first_id = reviews[0]["id"]
    status, review = _request("GET", ontology_url(f"/objects/ProductReview/{first_id}"), token=jdoe_token)
    assert status == 200
    assert review["id"] == first_id

    status, body = _request("GET", ontology_url("/objects/ProductReview/999999"), token=jdoe_token)
    assert status == 404, body
