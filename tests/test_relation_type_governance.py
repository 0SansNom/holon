"""End-to-end verification of relation-type governance:
"cardinalité et direction explicites ; extrémités existantes" — now a real,
enforced API instead of just true by construction of the hardcoded seed
list. Black-box over HTTP, same style as the other test modules. Requires
the stack running (`make up`).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import IDENTITY, KNOWLEDGE, _request


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


def test_seeded_relation_types_are_listed(jdoe_token: str) -> None:
    status, relations = _request("GET", f"{KNOWLEDGE}/relation-types", token=jdoe_token)
    assert status == 200, relations
    names = {r["name"] for r in relations}
    assert {"Order.customer", "SupportTicket.customer", "ProductReview.order"} <= names, relations


def test_get_single_relation_type_returns_full_detail(jdoe_token: str) -> None:
    status, relation = _request("GET", f"{KNOWLEDGE}/relation-types/Order.customer", token=jdoe_token)
    assert status == 200, relation
    assert relation["cardinality"] == "many_to_one", relation
    assert relation["source_property"] == "customerId", relation
    assert relation["target_object_type_urn"].endswith(":object-type:Customer"), relation


def test_admin_can_register_a_new_relation_type(jdoe_token: str, msmith_token: str) -> None:
    """`create_relation_type` has no upsert semantics (unlike the
    idempotent-by-design overlay tables elsewhere in this codebase) — a
    repeated test run against a stack that already has this row from a
    prior run correctly 409s rather than 201s. Both outcomes prove the
    same thing (the relation type exists), so both are accepted here.
    """
    status, created = _request(
        "POST",
        f"{KNOWLEDGE}/relation-types",
        token=msmith_token,
        body={
            "name": "SupportTicket.order",
            "source_object_type": "SupportTicket",
            "target_object_type": "Order",
            "source_property": "orderId",
            "cardinality": "many_to_one",
        },
    )
    assert status in (201, 409), created
    if status == 201:
        assert created["cardinality"] == "many_to_one", created

    status, relations = _request("GET", f"{KNOWLEDGE}/relation-types", token=jdoe_token)
    assert status == 200
    assert any(r["name"] == "SupportTicket.order" for r in relations), relations


def test_editor_cannot_register_a_relation_type(jdoe_token: str) -> None:
    """jdoe is editor-only (never admin, by construction — separation of
    duties, same as high-risk Action approval).
    """
    status, body = _request(
        "POST",
        f"{KNOWLEDGE}/relation-types",
        token=jdoe_token,
        body={
            "name": "ShouldNotExist.customer",
            "source_object_type": "Order",
            "target_object_type": "Customer",
            "source_property": "customerId",
            "cardinality": "many_to_one",
        },
    )
    assert status == 403, body


def test_nonexistent_target_object_type_is_rejected(msmith_token: str) -> None:
    """The actual enforcement of "extrémités existantes" — not just true by
    construction of a hardcoded list anymore.
    """
    status, body = _request(
        "POST",
        f"{KNOWLEDGE}/relation-types",
        token=msmith_token,
        body={
            "name": "Order.nonexistent",
            "source_object_type": "Order",
            "target_object_type": "NoSuchObjectType",
            "source_property": "somePropertyId",
            "cardinality": "many_to_one",
        },
    )
    assert status == 400, body
    assert "does not exist" in body["detail"], body


def test_invalid_cardinality_is_rejected(msmith_token: str) -> None:
    status, body = _request(
        "POST",
        f"{KNOWLEDGE}/relation-types",
        token=msmith_token,
        body={
            "name": "Order.badCardinality",
            "source_object_type": "Order",
            "target_object_type": "Customer",
            "source_property": "customerId",
            "cardinality": "sideways",
        },
    )
    assert status == 400, body
    assert "cardinality" in body["detail"], body


def test_duplicate_name_is_rejected(msmith_token: str) -> None:
    status, body = _request(
        "POST",
        f"{KNOWLEDGE}/relation-types",
        token=msmith_token,
        body={
            "name": "Order.customer",  # already seeded at startup
            "source_object_type": "Order",
            "target_object_type": "Customer",
            "source_property": "customerId",
            "cardinality": "many_to_one",
        },
    )
    assert status == 409, body
