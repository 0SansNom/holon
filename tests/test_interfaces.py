"""Interfaces — polymorphism
across ObjectTypes. An Interface is a checked contract (required
properties/actions), not a label: `_validate_implements` enforces it at
publish time, the same synchronous-validation treatment
`create_relation_type` already gives cardinality/endpoints. Uses
UUID-suffixed interface names throughout so a repeated run never
collides with a prior run's state (same idiom as
`test_relation_type_governance.py`). No real LLM calls.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

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


def _unique_name(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def test_editor_cannot_create_an_interface(jdoe_token: str) -> None:
    status, body = _request(
        "POST", f"{KNOWLEDGE}/interfaces", token=jdoe_token,
        body={"name": _unique_name("ShouldBeDenied"), "required_properties": []},
    )
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body


def test_admin_can_create_an_interface_and_read_it_back(msmith_token: str) -> None:
    name = _unique_name("HasCategory")
    status, created = _request(
        "POST", f"{KNOWLEDGE}/interfaces", token=msmith_token,
        body={"name": name, "required_properties": ["category"], "description": "test interface"},
    )
    assert status == 201, created
    assert created["required_properties"] == ["category"], created

    status, fetched = _request("GET", f"{KNOWLEDGE}/interfaces/{name}", token=msmith_token)
    assert status == 200, fetched
    assert fetched["name"] == name, fetched

    status, listed = _request("GET", f"{KNOWLEDGE}/interfaces", token=msmith_token)
    assert status == 200
    assert any(i["name"] == name for i in listed), listed


def test_unknown_interface_is_404(msmith_token: str) -> None:
    status, body = _request("GET", f"{KNOWLEDGE}/interfaces/{_unique_name('DoesNotExist')}", token=msmith_token)
    assert status == 404, body


def test_publish_rejects_a_missing_required_property(msmith_token: str) -> None:
    """Supplier has no `lifetimeValue` property — declaring conformance
    to an interface requiring it must fail at publish time, not silently
    succeed as a label with nothing behind it.
    """
    interface_name = _unique_name("HasLifetimeValue")
    status, _ = _request(
        "POST", f"{KNOWLEDGE}/interfaces", token=msmith_token,
        body={"name": interface_name, "required_properties": ["lifetimeValue"]},
    )
    assert status == 201

    status, draft = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/versions", token=msmith_token,
        body={"implements": [interface_name]},
    )
    assert status == 201, draft

    status, publish_result = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/versions/{draft['version']}/publish", token=msmith_token
    )
    assert status == 400, publish_result
    assert "missing required property" in publish_result["detail"], publish_result

    status, live = _request("GET", f"{KNOWLEDGE}/ontology/Supplier", token=msmith_token)
    assert interface_name not in (live.get("implements") or []), "a rejected publish must not leak into the live definition"


def test_publish_rejects_a_missing_required_action(msmith_token: str) -> None:
    """Customer really has `putOnCreditHold` but not this made-up action —
    the required-action check must be against real, registered Actions.
    """
    interface_name = _unique_name("HasFakeAction")
    status, _ = _request(
        "POST", f"{KNOWLEDGE}/interfaces", token=msmith_token,
        body={"name": interface_name, "required_actions": ["thisAcionDoesNotExist"]},
    )
    assert status == 201

    status, draft = _request(
        "POST", f"{KNOWLEDGE}/ontology/Customer/versions", token=msmith_token,
        body={"implements": [interface_name]},
    )
    assert status == 201, draft

    status, publish_result = _request(
        "POST", f"{KNOWLEDGE}/ontology/Customer/versions/{draft['version']}/publish", token=msmith_token
    )
    assert status == 400, publish_result
    assert "missing required action" in publish_result["detail"], publish_result


def test_conformant_implements_publishes_and_is_polymorphically_queryable(msmith_token: str, jdoe_token: str) -> None:
    """The full happy path: an interface Supplier genuinely satisfies
    (real property, no required actions) publishes cleanly, and
    `GET /interfaces/{name}/objects` resolves real Supplier instances
    through it — the same masked/serving-store-backed read every other
    list endpoint uses, just reached polymorphically.
    """
    interface_name = _unique_name("HasCountry")
    status, _ = _request(
        "POST", f"{KNOWLEDGE}/interfaces", token=msmith_token,
        body={"name": interface_name, "required_properties": ["country"]},
    )
    assert status == 201

    status, draft = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/versions", token=msmith_token,
        body={"implements": [interface_name]},
    )
    assert status == 201, draft

    status, published = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/versions/{draft['version']}/publish", token=msmith_token
    )
    assert status == 200, published
    assert published["implements"] == [interface_name], published

    status, live = _request("GET", f"{KNOWLEDGE}/ontology/Supplier", token=msmith_token)
    assert live["implements"] == [interface_name], live

    status, objects = _request("GET", f"{KNOWLEDGE}/interfaces/{interface_name}/objects", token=jdoe_token)
    assert status == 200, objects
    assert objects, "expected at least one Supplier instance"
    assert all(o["_objectType"] == "Supplier" for o in objects), objects
    assert all("country" in o for o in objects), objects


def test_objects_endpoint_404s_for_an_unknown_interface(jdoe_token: str) -> None:
    status, body = _request(
        "GET", f"{KNOWLEDGE}/interfaces/{_unique_name('NeverRegistered')}/objects", token=jdoe_token
    )
    assert status == 404, body
