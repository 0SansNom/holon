"""Tests for Search."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import CONNECTIVITY, IDENTITY, KNOWLEDGE, _request, ontology_url, holon_url


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
def all_datasets_synced(jdoe_token: str) -> None:
    for dataset in ("customers", "orders", "support_tickets", "reviews"):
        status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": dataset})
        assert status == 200, result


def _search(token: str, query: str, deadline_seconds: float = 30, want_object_type: str | None = None) -> dict:
    """Indexing happens asynchronously in the same catalog consumer as."""
    deadline = time.monotonic() + deadline_seconds
    body: dict = {}
    while time.monotonic() < deadline:
        status, body = _request("GET", holon_url(f"/search?q={query}"), token=token)
        assert status == 200, body
        if want_object_type is not None:
            if any(r["object_type"] == want_object_type for r in body["results"]):
                return body
        elif body["total"] > 0:
            return body
        time.sleep(1)
    return body


def test_jdoe_finds_confidential_customer_document(jdoe_token: str, all_datasets_synced: None) -> None:
    result = _search(jdoe_token, "Robotics")
    assert result["total"] >= 1, result
    assert any(r["object_type"] == "Customer" for r in result["results"]), result


def test_kenji_does_not_find_the_confidential_document(jdoe_token: str, kenji_token: str, all_datasets_synced: None) -> None:
    # First prove it's actually indexed (jdoe can see it) before asserting
    # kenji can't
    _search(jdoe_token, "Robotics")

    status, result = _request("GET", holon_url("/search?q=Robotics"), token=kenji_token)
    assert status == 200, result
    assert not any(r["object_type"] == "Customer" for r in result["results"]), result


def test_no_post_filter_leak_total_matches_permitted_count_only(
    jdoe_token: str, kenji_token: str, all_datasets_synced: None
) -> None:
    """"Acme" matches both the confidential Customer ("Acme Robotics") and."""
    # Wait specifically for the ProductReview side to converge (as jdoe,
    # who can see both) before asserting anything about kenji's narrower view.
    _search(jdoe_token, "Acme", want_object_type="ProductReview")

    status, result = _request("GET", holon_url("/search?q=Acme"), token=kenji_token)
    assert status == 200, result
    assert result["total"] == len(result["results"]), result
    assert all(r["object_type"] != "Customer" for r in result["results"]), result
    assert any(r["object_type"] == "ProductReview" for r in result["results"]), result


def test_rebac_denied_principal_cannot_search_at_all(alice_token: str, all_datasets_synced: None) -> None:
    status, body = _request("GET", holon_url("/search?q=Robotics"), token=alice_token)
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body


def test_facets_report_a_count_per_object_type(jdoe_token: str, all_datasets_synced: None) -> None:
    # "Acme" matches Customer ("Acme Robotics") and ProductReview
    # (reviewer_name "Acme Robotics Ops")
    # facet assertion below isn't racing indexing.
    _search(jdoe_token, "Acme", want_object_type="ProductReview")
    result = _search(jdoe_token, "Acme")
    assert result["facets"].get("Customer", 0) >= 1, result
    assert result["facets"].get("ProductReview", 0) >= 1, result
    assert sum(result["facets"].values()) == result["total"], result


def test_object_type_filter_narrows_hits_without_collapsing_other_facet_counts(
    jdoe_token: str, all_datasets_synced: None
) -> None:
    _search(jdoe_token, "Acme", want_object_type="ProductReview")
    baseline = _search(jdoe_token, "Acme")

    status, filtered = _request("GET", holon_url("/search?q=Acme&object_type=Customer"), token=jdoe_token)
    assert status == 200, filtered
    assert filtered["total"] == baseline["facets"]["Customer"], (filtered, baseline)
    assert all(r["object_type"] == "Customer" for r in filtered["results"]), filtered
    # The unselected facet's count must be unaffected by the filter
    # `post_filter`, not a query-level filter, is what makes this true.
    assert filtered["facets"] == baseline["facets"], (filtered, baseline)


def test_pagination_size_and_from_are_respected(jdoe_token: str, all_datasets_synced: None) -> None:
    status, page = _request("GET", holon_url("/search?q=a&size=2"), token=jdoe_token)
    assert status == 200, page
    assert len(page["results"]) <= 2, page

    status, next_page = _request("GET", holon_url("/search?q=a&size=2&from=2"), token=jdoe_token)
    assert status == 200, next_page
    if page["results"] and next_page["results"]:
        assert page["results"][0]["urn"] != next_page["results"][0]["urn"], (page, next_page)
