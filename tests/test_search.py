"""End-to-end verification of unified search (SAS R8.6) — entitlement
tokens derived from the ReBAC/ABAC graph, filtered at the source inside
OpenSearch's own query, never post-filtered in application code. Black-box
over HTTP, same style as the other test modules. Requires the stack
running (`make up`), including the new `opensearch` container.
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
def all_datasets_synced(jdoe_token: str) -> None:
    for dataset in ("customers", "orders", "support_tickets", "reviews"):
        status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": dataset})
        assert status == 200, result


def _search(token: str, query: str, deadline_seconds: float = 30, want_object_type: str | None = None) -> dict:
    """Indexing happens asynchronously in the same catalog consumer as
    materialization — poll rather than assume it already converged, same
    convergence race as everywhere else in this suite. A plain "total > 0"
    isn't enough when a query matches documents from *different* datasets
    that converge independently (e.g. "Acme" matches both Customer and
    ProductReview) — `want_object_type`, when given, waits specifically
    for that dataset's own materialization to show up.
    """
    deadline = time.monotonic() + deadline_seconds
    body: dict = {}
    while time.monotonic() < deadline:
        status, body = _request("GET", f"{KNOWLEDGE}/search?q={query}", token=token)
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
    # kenji can't — otherwise a 0-result search proves nothing.
    _search(jdoe_token, "Robotics")

    status, result = _request("GET", f"{KNOWLEDGE}/search?q=Robotics", token=kenji_token)
    assert status == 200, result
    assert not any(r["object_type"] == "Customer" for r in result["results"]), result


def test_no_post_filter_leak_total_matches_permitted_count_only(
    jdoe_token: str, kenji_token: str, all_datasets_synced: None
) -> None:
    """"Acme" matches both the confidential Customer ("Acme Robotics") and
    a public ProductReview (reviewer_name "Acme Robotics Ops"). kenji is
    ABAC-denied on the former, granted on the latter (§ increment #8's
    established proof point). R8.6: `total` must equal exactly the
    permitted count — not the true cross-object-type total with a
    permitted subset silently returned underneath it.
    """
    # Wait specifically for the ProductReview side to converge (as jdoe,
    # who can see both) before asserting anything about kenji's narrower view.
    _search(jdoe_token, "Acme", want_object_type="ProductReview")

    status, result = _request("GET", f"{KNOWLEDGE}/search?q=Acme", token=kenji_token)
    assert status == 200, result
    assert result["total"] == len(result["results"]), result
    assert all(r["object_type"] != "Customer" for r in result["results"]), result
    assert any(r["object_type"] == "ProductReview" for r in result["results"]), result


def test_rebac_denied_principal_cannot_search_at_all(alice_token: str, all_datasets_synced: None) -> None:
    status, body = _request("GET", f"{KNOWLEDGE}/search?q=Robotics", token=alice_token)
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body
