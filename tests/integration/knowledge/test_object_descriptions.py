"""Tests for Object Descriptions."""

from __future__ import annotations

import pytest
from conftest import _request, holon_url, ontology_url


OBJECT_TYPES = ["Customer", "Order", "SupportTicket", "ProductReview", "Supplier", "InventoryLevel"]
ACTIONS = ["Customer.putOnCreditHold", "Customer.closeAccount"]


@pytest.mark.parametrize("object_type", OBJECT_TYPES)
def test_object_type_has_a_non_empty_description(jdoe_token: str, object_type: str) -> None:
    status, body = _request("GET", ontology_url(f"/objectTypes/{object_type}"), token=jdoe_token)
    assert status == 200, body
    assert body["description"].strip(), body


def test_list_actions_returns_both_actions_with_descriptions(jdoe_token: str) -> None:
    status, body = _request("GET", holon_url("/actions"), token=jdoe_token)
    assert status == 200, body
    by_name = {a["name"]: a for a in body}
    for action_name in ACTIONS:
        assert action_name in by_name, body
        assert by_name[action_name]["description"].strip(), by_name[action_name]


@pytest.mark.parametrize("action_name", ACTIONS)
def test_get_action_by_name_has_a_description(jdoe_token: str, action_name: str) -> None:
    status, body = _request("GET", holon_url(f"/actions/{action_name}"), token=jdoe_token)
    assert status == 200, body
    assert body["description"].strip(), body


def test_unknown_action_is_404(jdoe_token: str) -> None:
    status, body = _request("GET", holon_url("/actions/DoesNotExist.foo"), token=jdoe_token)
    assert status == 404, body
