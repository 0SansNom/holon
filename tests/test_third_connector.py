"""End-to-end verification of the third connector — a generic REST source
(§9.1) — and the `ProductReview` ObjectType/relation it feeds. Black-box
over HTTP, same style as the other test modules. Requires the stack
running (`make up`).
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

# Mirrors docker/reviews-api/reviews.json — order 1 has exactly one review,
# order 3 (Customer 1's "Custom Automation Software") has none.
ORDER_WITH_REVIEW = 1
ORDER_WITHOUT_REVIEW = 3


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
def kenji_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:kenji")


@pytest.fixture(scope="session")
def alice_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:alice")


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
    # (R1.5) — poll rather than assume it already converged by the time
    # this test runs, same convergence race as the catalog itself.
    deadline = time.monotonic() + 30
    object_type: dict = {}
    while time.monotonic() < deadline:
        status, object_type = _request("GET", f"{KNOWLEDGE}/ontology/ProductReview", token=jdoe_token)
        assert status == 200, object_type
        if object_type["classification"] == "public":
            break
        time.sleep(1)
    assert object_type["classification"] == "public", object_type


def test_relation_traversal_from_order_returns_the_right_review(jdoe_token: str, reviews_synced: dict) -> None:
    status, reviews = _request("GET", f"{KNOWLEDGE}/objects/Order/{ORDER_WITH_REVIEW}/reviews", token=jdoe_token)
    assert status == 200, reviews
    assert len(reviews) == 1
    assert reviews[0]["order_id"] == ORDER_WITH_REVIEW


def test_relation_traversal_for_unreviewed_order_is_empty(jdoe_token: str, reviews_synced: dict) -> None:
    status, reviews = _request("GET", f"{KNOWLEDGE}/objects/Order/{ORDER_WITHOUT_REVIEW}/reviews", token=jdoe_token)
    assert status == 200
    assert reviews == []


def test_abac_only_restricts_confidential_data_not_public_data(kenji_token: str, reviews_synced: dict) -> None:
    """kenji (non-EU country) gets confidential fields masked on
    Customer/Order elsewhere in this build (R8.7) — but ProductReview has
    no confidential column at all, so there's nothing to mask; every
    field comes back intact. Proves ABAC's country restriction is
    conditional on what's actually confidential, not a blanket hurdle.
    """
    status, reviews = _request("GET", f"{KNOWLEDGE}/objects/ProductReview", token=kenji_token)
    assert status == 200, reviews


def test_rebac_still_denies_a_principal_with_no_workspace_relation(alice_token: str, reviews_synced: dict) -> None:
    status, body = _request("GET", f"{KNOWLEDGE}/objects/ProductReview", token=alice_token)
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body


def test_product_review_objects_are_directly_resolvable(jdoe_token: str, reviews_synced: dict) -> None:
    status, reviews = _request("GET", f"{KNOWLEDGE}/objects/ProductReview", token=jdoe_token)
    assert status == 200
    assert len(reviews) >= 1

    first_id = reviews[0]["id"]
    status, review = _request("GET", f"{KNOWLEDGE}/objects/ProductReview/{first_id}", token=jdoe_token)
    assert status == 200
    assert review["id"] == first_id

    status, body = _request("GET", f"{KNOWLEDGE}/objects/ProductReview/999999", token=jdoe_token)
    assert status == 404, body
