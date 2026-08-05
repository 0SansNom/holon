"""Rebuild test for `serving_store`/OpenSearch:
Both are reconstructible from Iceberg without manual intervention beyond `/sync`.
Wipe both sinks directly, trigger one `/sync`, and confirm the running
consumer's existing materialization step repopulates them identically.

White-box for the wipe (direct `asyncpg`/OpenSearch REST access, same
treatment as `test_projection_rebuild.py`), black-box HTTP for
everything else. Requires the stack running (`make up`).
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request

import asyncio
import asyncpg
import pytest

IDENTITY = "http://localhost:8001"
CONNECTIVITY = "http://localhost:8002"
KNOWLEDGE = "http://localhost:8003"
OPENSEARCH = "http://localhost:9200"
# Both read from the environment, not hardcoded — CI generates its own
# .env with different values than a dev's local one (see
# .github/workflows/tests.yml, which sets its own
# OPENSEARCH_ADMIN_PASSWORD and POSTGRES_PASSWORD explicitly), so
# hardcoding either only ever worked by coincidence locally. Defaults
# match .env.example's dev convenience values for a plain
# `pytest tests/` run against `make up`.
OPENSEARCH_PASSWORD = os.environ.get("OPENSEARCH_ADMIN_PASSWORD", "HolonSearch#2026")

TENANT_ID = "acme"
KNOWLEDGE_DB_URL = f"postgresql://holon:{os.environ.get('POSTGRES_PASSWORD', 'holon12345')}@localhost:5432/holon_knowledge"


def _request(method: str, url: str, *, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _opensearch_request(method: str, path: str, *, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{OPENSEARCH}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    credentials = base64.b64encode(f"admin:{OPENSEARCH_PASSWORD}".encode()).decode()
    req.add_header("Authorization", f"Basic {credentials}")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read())


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


async def _delete_customer_serving_store_rows() -> None:
    conn = await asyncpg.connect(KNOWLEDGE_DB_URL)
    try:
        await conn.execute("DELETE FROM object_instance WHERE object_type = 'Customer' AND tenant_id = $1", TENANT_ID)
    finally:
        await conn.close()


def _sync_and_wait(jdoe_token: str) -> dict:
    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "customers"})
    assert status == 200, result

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status, datasets = _request("GET", f"{KNOWLEDGE}/catalog/datasets", token=jdoe_token)
        assert status == 200
        customers = next((d for d in datasets if d["urn"] == result["dataset_urn"]), None)
        if customers and customers["snapshot_id"] == result["snapshot_id"]:
            return result
        time.sleep(1)
    pytest.fail("catalog did not converge to the new customers snapshot in time")


def test_serving_store_and_search_reconstruct_from_iceberg_after_being_wiped(jdoe_token: str) -> None:
    # 1. Baseline: Customer must already be synced/materialized (every
    # earlier test module in this suite ensures this).
    status, before_rows = _request("GET", f"{KNOWLEDGE}/objects/Customer", token=jdoe_token)
    assert status == 200, before_rows
    assert len(before_rows) >= 1, "no Customer data materialized yet — run a /sync first (make demo)"
    expected_count = len(before_rows)

    status, before_search = _request("GET", f"{KNOWLEDGE}/search?q=Acme", token=jdoe_token)
    assert status == 200, before_search
    assert before_search["total"] >= 1, before_search

    # 2. Wipe both sinks directly — nothing left of either projection.
    # A materialization from an *earlier* test in the suite can still be
    # landing asynchronously (Kafka consumer lag), racing this delete with
    # a concurrent index write — retry the wipe until it actually
    # converges to empty rather than assuming one pass is atomic against
    # a concurrent writer.
    asyncio.run(_delete_customer_serving_store_rows())
    deadline = time.monotonic() + 30
    wiped_search: dict = {}
    while time.monotonic() < deadline:
        _opensearch_request(
            "POST",
            "/holon-search/_delete_by_query",
            body={
                "query": {"bool": {"filter": [{"term": {"object_type": "Customer"}}]}},
                "conflicts": "proceed",  # skip, don't abort, on a concurrent index write
            },
        )
        _opensearch_request("POST", "/holon-search/_refresh", body={})
        status, wiped_search = _request("GET", f"{KNOWLEDGE}/search?q=Acme", token=jdoe_token)
        assert status == 200, wiped_search
        if not any(hit["object_type"] == "Customer" for hit in wiped_search["results"]):
            break
        asyncio.run(_delete_customer_serving_store_rows())  # a concurrent materialize may have re-inserted this too
        time.sleep(2)
    # "Acme" also matches unrelated ProductReview text (reviews mention
    # product/company names) — only Customer docs were wiped, so assert
    # their specific absence, not a blanket zero total.
    assert not any(hit["object_type"] == "Customer" for hit in wiped_search["results"]), wiped_search

    status, wiped_rows = _request("GET", f"{KNOWLEDGE}/objects/Customer", token=jdoe_token)
    assert status == 200
    assert all(row.get("degraded") for row in wiped_rows), wiped_rows  # federated fallback, not a 500

    # 3. One ordinary /sync — no manual intervention beyond that — and
    # the running consumer's existing `_materialize_sync` re-scans
    # Iceberg and repopulates both sinks from scratch.
    _sync_and_wait(jdoe_token)

    deadline = time.monotonic() + 30
    rebuilt_rows: list = []
    while time.monotonic() < deadline:
        status, rebuilt_rows = _request("GET", f"{KNOWLEDGE}/objects/Customer", token=jdoe_token)
        assert status == 200, rebuilt_rows
        if len(rebuilt_rows) == expected_count and not any(row.get("degraded") for row in rebuilt_rows):
            break
        time.sleep(1)
    assert len(rebuilt_rows) == expected_count, rebuilt_rows
    assert not any(row.get("degraded") for row in rebuilt_rows), rebuilt_rows

    # Same "Acme" ambiguity as the wipe check above — poll for a Customer
    # hit specifically.
    deadline = time.monotonic() + 30
    rebuilt_search: dict = {}
    while time.monotonic() < deadline:
        status, rebuilt_search = _request("GET", f"{KNOWLEDGE}/search?q=Acme", token=jdoe_token)
        assert status == 200, rebuilt_search
        if any(hit["object_type"] == "Customer" for hit in rebuilt_search["results"]):
            break
        time.sleep(1)
    assert any(hit["object_type"] == "Customer" for hit in rebuilt_search["results"]), rebuilt_search
