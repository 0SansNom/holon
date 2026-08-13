"""End-to-end verification of the MongoDB connector and the `SupportTicket`
ObjectType/relation it feeds. Black-box over HTTP. Requires the stack running (`make up`).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import CONNECTIVITY, IDENTITY, KNOWLEDGE, TENANT_ID, _request


WORKSPACE_ID = "main"

# Mirrors docker/mongo-init/init.js — customer 7 has 2 tickets, customer 3 has none.
CUSTOMER_WITH_TICKETS = 7
EXPECTED_TICKET_COUNT_FOR_CUSTOMER_WITH_TICKETS = 2
CUSTOMER_WITHOUT_TICKETS = 3


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
def tickets_synced(jdoe_token: str) -> dict:
    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "support_tickets"})
    assert status == 200, result
    assert result["dataset_urn"] == f"hl:{TENANT_ID}:{WORKSPACE_ID}:dataset:support_tickets"
    return result


def test_mongo_connector_is_distinct_from_the_postgres_one(jdoe_token: str, tickets_synced: dict) -> None:
    status, syncs = _request("GET", f"{CONNECTIVITY}/syncs", token=jdoe_token)
    assert status == 200

    matching = [s for s in syncs if s["dataset_version_urn"] == tickets_synced["dataset_version_urn"]]
    assert len(matching) == 1, syncs
    assert matching[0]["connector_urn"] == f"hl:{TENANT_ID}:global:connector:mongodb-support-desk"


def test_support_ticket_classification_is_internal_not_confidential(jdoe_token: str, tickets_synced: dict) -> None:
    status, object_type = _request("GET", f"{KNOWLEDGE}/ontology/SupportTicket", token=jdoe_token)
    assert status == 200, object_type
    # No confidential column in the mapping — a deliberate contrast to Customer/Order.
    assert object_type["classification"] == "internal", object_type


def test_relation_traversal_returns_the_right_tickets(jdoe_token: str, tickets_synced: dict) -> None:
    status, tickets = _request(
        "GET", f"{KNOWLEDGE}/objects/Customer/{CUSTOMER_WITH_TICKETS}/tickets", token=jdoe_token
    )
    assert status == 200, tickets
    assert len(tickets) == EXPECTED_TICKET_COUNT_FOR_CUSTOMER_WITH_TICKETS
    assert all(t["customer_id"] == CUSTOMER_WITH_TICKETS for t in tickets)


def test_relation_traversal_for_customer_without_tickets_is_empty(jdoe_token: str, tickets_synced: dict) -> None:
    status, tickets = _request(
        "GET", f"{KNOWLEDGE}/objects/Customer/{CUSTOMER_WITHOUT_TICKETS}/tickets", token=jdoe_token
    )
    assert status == 200
    assert tickets == []


def test_ticket_traversal_goes_through_the_same_pdp(alice_token: str, tickets_synced: dict) -> None:
    status, body = _request(
        "GET", f"{KNOWLEDGE}/objects/Customer/{CUSTOMER_WITH_TICKETS}/tickets", token=alice_token
    )
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body


def test_support_ticket_objects_are_directly_resolvable(jdoe_token: str, tickets_synced: dict) -> None:
    status, tickets = _request("GET", f"{KNOWLEDGE}/objects/SupportTicket", token=jdoe_token)
    assert status == 200
    assert len(tickets) >= EXPECTED_TICKET_COUNT_FOR_CUSTOMER_WITH_TICKETS

    first_id = tickets[0]["id"]
    status, ticket = _request("GET", f"{KNOWLEDGE}/objects/SupportTicket/{first_id}", token=jdoe_token)
    assert status == 200
    assert ticket["id"] == first_id

    status, body = _request("GET", f"{KNOWLEDGE}/objects/SupportTicket/999999", token=jdoe_token)
    assert status == 404, body
