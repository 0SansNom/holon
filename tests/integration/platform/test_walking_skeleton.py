"""Tests for Walking Skeleton."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

import jwt
import pytest
from conftest import CONNECTIVITY, EXPERIENCE, IDENTITY, KNOWLEDGE, TENANT_ID, _request, ontology_url, holon_url


JWT_SECRET = "dev-only-walking-skeleton-secret"  # matches docker-compose.yml x-app-env
WORKSPACE_ID = "main"
USER_URN = f"hl:{TENANT_ID}:global:user:jdoe"


def _mint_token(tenant_id: str, urn: str, country: str | None = None) -> str:
    now = int(time.time())
    payload = {
        "sub": urn,
        "type": "user",
        "tenant_id": tenant_id,
        "display_name": "test",
        "on_behalf_of": None,
        "country": country,
        "iat": now,
        "exp": now + 3600,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


@pytest.fixture(scope="session")
def token() -> str:
    for name, url in [
        ("identity", IDENTITY),
        ("connectivity", CONNECTIVITY),
        ("knowledge", KNOWLEDGE),
        ("experience", EXPERIENCE),
    ]:
        deadline = time.monotonic() + 60
        healthy = False
        while time.monotonic() < deadline:
            status, _ = _request("GET", f"{url}/health")
            if status == 200:
                healthy = True
                break
            time.sleep(1.5)
        if not healthy:
            pytest.fail(f"{name} never became healthy")
    return _mint_token(TENANT_ID, USER_URN, country="FR")  # matches jdoe's seeded ABAC attribute


@pytest.fixture(scope="session")
def synced(token: str) -> dict:
    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=token)
    assert status == 200, result
    return result


def test_sync_lands_a_new_dataset_version(synced: dict) -> None:
    assert synced["dataset_urn"] == f"hl:{TENANT_ID}:{WORKSPACE_ID}:dataset:customers"
    assert synced["row_count"] == 10


def test_dataset_is_catalogued(token: str, synced: dict) -> None:
    deadline = time.monotonic() + 30
    customers_entry = None
    while time.monotonic() < deadline:
        status, datasets = _request("GET", holon_url("/catalog/datasets"), token=token)
        assert status == 200
        customers_entry = next((d for d in datasets if d["urn"] == synced["dataset_urn"]), None)
        if customers_entry is not None and customers_entry["snapshot_id"] == synced["snapshot_id"]:
            break
        time.sleep(1)
    assert customers_entry is not None, "catalog did not converge in time"
    assert customers_entry["urn"] == synced["dataset_urn"]
    assert customers_entry["snapshot_id"] == synced["snapshot_id"]


def test_customer_objects_are_resolvable(token: str, synced: dict) -> None:
    status, customers = _request("GET", ontology_url("/objects/Customer"), token=token)
    assert status == 200
    assert len(customers) == synced["row_count"]
    assert {"id", "name", "email", "country", "segment", "lifetime_value"} <= customers[0].keys()


def test_single_customer_lookup(token: str) -> None:
    status, customer = _request("GET", ontology_url("/objects/Customer/1"), token=token)
    assert status == 200
    assert customer["id"] == 1

    status, _ = _request("GET", ontology_url("/objects/Customer/9999"), token=token)
    assert status == 404


def test_lineage_links_dataset_version_to_object_type(token: str, synced: dict) -> None:
    object_type_urn = f"hl:{TENANT_ID}:{WORKSPACE_ID}:object-type:Customer"
    status, edges = _request("GET", holon_url(f"/lineage/{synced['dataset_version_urn']}"), token=token)
    assert status == 200
    assert any(e["target_urn"] == object_type_urn and e["relation"] == "maps_to" for e in edges)


def test_cross_tenant_read_is_rejected(synced: dict) -> None:
    foreign_token = _mint_token("other-tenant", "hl:other-tenant:global:user:eve")
    status, body = _request("GET", ontology_url("/objects/Customer"), token=foreign_token)
    assert status == 404, body


def test_dashboard_only_talks_to_the_knowledge_api() -> None:
    status, _ = _request("GET", f"{EXPERIENCE}/health")
    assert status == 200

    with urllib.request.urlopen(f"{EXPERIENCE}/", timeout=10) as response:
        assert response.status == 200
        html = response.read().decode()
    assert '<div id="root">' in html

    script_srcs = re.findall(r'<script[^>]+src="(/assets/[^"]+\.js)"', html)
    assert script_srcs, "index.html should reference at least one built JS bundle"

    bundle = ""
    for src in script_srcs:
        with urllib.request.urlopen(f"{EXPERIENCE}{src}", timeout=10) as response:
            bundle += response.read().decode(errors="ignore")

    for forbidden in ("duckdb", "s3://", "psycopg", "asyncpg", "iceberg-rest"):
        assert forbidden not in bundle.lower(), f"shipped SPA bundle references storage internals: {forbidden!r}"
    assert "/objects/" in bundle
    assert "/lineage/" in bundle
