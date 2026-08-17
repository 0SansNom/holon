"""Tests for Column Lineage."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import CONNECTIVITY, IDENTITY, KNOWLEDGE, TENANT_ID, _request, ontology_url, holon_url


WORKSPACE_ID = "main"

# Mirrors services/knowledge/app/ontology.py's CUSTOMER_PROPERTY_MAPPING
# duplicated on purpose: this test talks to the API only, never imports
# service internals.
EXPECTED_PROPERTY_MAPPING = {
    "id": "id",
    "name": "name",
    "email": "email",
    "country": "country",
    "segment": "segment",
    "lifetimeValue": "lifetime_value",
    "updatedAt": "updated_at",
}


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
def synced(jdoe_token: str) -> dict:
    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token)
    assert status == 200, result
    return result


def test_column_level_edges_are_captured(jdoe_token: str, synced: dict) -> None:
    dataset_version_urn = synced["dataset_version_urn"]
    object_type_urn = f"hl:{TENANT_ID}:{WORKSPACE_ID}:object-type:Customer"

    deadline = time.monotonic() + 30
    edges: list = []
    while time.monotonic() < deadline:
        status, edges = _request("GET", holon_url(f"/lineage/{dataset_version_urn}"), token=jdoe_token)
        assert status == 200, edges
        column_edges = [e for e in edges if e["source_column"]]
        if len(column_edges) == len(EXPECTED_PROPERTY_MAPPING):
            break
        time.sleep(1)

    dataset_level = [e for e in edges if not e["source_column"] and not e["target_property"]]
    assert any(e["target_urn"] == object_type_urn and e["relation"] == "maps_to" for e in dataset_level), edges

    column_edges = {(e["source_column"], e["target_property"]) for e in edges if e["source_column"]}
    expected = {(source_column, prop) for prop, source_column in EXPECTED_PROPERTY_MAPPING.items()}
    assert column_edges == expected, edges


def test_object_type_classification_is_computed_not_hardcoded(jdoe_token: str, synced: dict) -> None:
    status, object_type = _request("GET", ontology_url("/objectTypes/Customer"), token=jdoe_token)
    assert status == 200, object_type
    # email + lifetime_value are confidential (catalog.py's CUSTOMERS_COLUMN_CLASSIFICATION);
    # most_restrictive() over all mapped columns must land on confidential.
    assert object_type["classification"] == "confidential", object_type


def test_authorization_outcomes_are_unaffected_by_the_switch(jdoe_token: str, kenji_token: str, synced: dict) -> None:
    status, _ = _request("GET", ontology_url("/objects/Customer"), token=jdoe_token)
    assert status == 200

    # kenji (disallowed country) is still ReBAC-granted the read;
    # ABAC now masks the confidential columns instead of denying the
    # whole object (see test_authorization.py for the dedicated case).
    status, customers = _request("GET", ontology_url("/objects/Customer"), token=kenji_token)
    assert status == 200, customers
    assert all(c["email"] is None for c in customers), customers
