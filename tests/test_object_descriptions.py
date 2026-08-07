"""Verification that every `Action` and `ObjectType` carries a natural-language description.
Black-box over HTTP. Requires the stack running (`make up`).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import IDENTITY, KNOWLEDGE


OBJECT_TYPES = ["Customer", "Order", "SupportTicket", "ProductReview", "Supplier", "InventoryLevel"]
ACTIONS = ["Customer.putOnCreditHold", "Customer.closeAccount"]


def _request(method: str, url: str, *, token: str | None = None):
    req = urllib.request.Request(url, method=method)
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
        data = json.dumps({"principal_urn": principal_urn, "client_secret": f"{local_name}-dev-secret"}).encode()
        req = urllib.request.Request(f"{IDENTITY}/token", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read())["access_token"]
        except urllib.error.HTTPError:
            time.sleep(1.5)
    pytest.fail(f"could not mint a token for {principal_urn}")


@pytest.mark.parametrize("object_type", OBJECT_TYPES)
def test_object_type_has_a_non_empty_description(jdoe_token: str, object_type: str) -> None:
    status, body = _request("GET", f"{KNOWLEDGE}/ontology/{object_type}", token=jdoe_token)
    assert status == 200, body
    assert body["description"].strip(), body


def test_list_actions_returns_both_actions_with_descriptions(jdoe_token: str) -> None:
    status, body = _request("GET", f"{KNOWLEDGE}/actions", token=jdoe_token)
    assert status == 200, body
    by_name = {a["name"]: a for a in body}
    for action_name in ACTIONS:
        assert action_name in by_name, body
        assert by_name[action_name]["description"].strip(), by_name[action_name]


@pytest.mark.parametrize("action_name", ACTIONS)
def test_get_action_by_name_has_a_description(jdoe_token: str, action_name: str) -> None:
    status, body = _request("GET", f"{KNOWLEDGE}/actions/{action_name}", token=jdoe_token)
    assert status == 200, body
    assert body["description"].strip(), body


def test_unknown_action_is_404(jdoe_token: str) -> None:
    status, body = _request("GET", f"{KNOWLEDGE}/actions/DoesNotExist.foo", token=jdoe_token)
    assert status == 404, body
