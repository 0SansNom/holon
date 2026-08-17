"""Tests for Interfaces."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

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


def _unique_name(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def test_editor_cannot_create_an_interface(jdoe_token: str) -> None:
    status, body = _request(
        "POST", ontology_url("/interfaceTypes"), token=jdoe_token,
        body={"name": _unique_name("ShouldBeDenied"), "required_properties": []},
    )
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body


def test_admin_can_create_an_interface_and_read_it_back(msmith_token: str) -> None:
    name = _unique_name("HasCategory")
    status, created = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": name, "required_properties": ["category"], "description": "test interface"},
    )
    assert status == 201, created
    assert created["required_properties"] == ["category"], created

    status, fetched = _request("GET", ontology_url(f"/interfaceTypes/{name}"), token=msmith_token)
    assert status == 200, fetched
    assert fetched["name"] == name, fetched

    status, listed = _request("GET", ontology_url("/interfaceTypes"), token=msmith_token)
    assert status == 200
    assert any(i["name"] == name for i in listed), listed


def test_unknown_interface_is_404(msmith_token: str) -> None:
    status, body = _request("GET", ontology_url(f"/interfaceTypes/{_unique_name('DoesNotExist')}"), token=msmith_token)
    assert status == 404, body


def test_publish_rejects_a_missing_required_property(msmith_token: str) -> None:
    """Supplier has no `lifetimeValue` property."""
    interface_name = _unique_name("HasLifetimeValue")
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": interface_name, "required_properties": ["lifetimeValue"]},
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url("/objectTypes/Supplier/versions"), token=msmith_token,
        body={"implements": [interface_name]},
    )
    assert status == 201, draft

    status, publish_result = _request(
        "POST", ontology_url(f"/objectTypes/Supplier/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 400, publish_result
    assert "missing required property" in publish_result["detail"], publish_result

    status, live = _request("GET", ontology_url("/objectTypes/Supplier"), token=msmith_token)
    assert interface_name not in (live.get("implements") or []), "a rejected publish must not leak into the live definition"


def test_publish_rejects_a_missing_required_action(msmith_token: str) -> None:
    """Customer really has `putOnCreditHold` but not this made-up action."""
    interface_name = _unique_name("HasFakeAction")
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": interface_name, "required_actions": ["thisAcionDoesNotExist"]},
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url("/objectTypes/Customer/versions"), token=msmith_token,
        body={"implements": [interface_name]},
    )
    assert status == 201, draft

    status, publish_result = _request(
        "POST", ontology_url(f"/objectTypes/Customer/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 400, publish_result
    assert "missing required action" in publish_result["detail"], publish_result


def test_conformant_implements_publishes_and_is_polymorphically_queryable(msmith_token: str, jdoe_token: str) -> None:
    """The full happy path: an interface Supplier genuinely satisfies."""
    interface_name = _unique_name("HasCountry")
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": interface_name, "required_properties": ["country"]},
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url("/objectTypes/Supplier/versions"), token=msmith_token,
        body={"implements": [interface_name]},
    )
    assert status == 201, draft

    status, published = _request(
        "POST", ontology_url(f"/objectTypes/Supplier/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 200, published
    assert published["implements"] == [interface_name], published

    status, live = _request("GET", ontology_url("/objectTypes/Supplier"), token=msmith_token)
    assert live["implements"] == [interface_name], live

    status, objects = _request("GET", ontology_url(f"/interfaceTypes/{interface_name}/objects"), token=jdoe_token)
    assert status == 200, objects
    assert objects, "expected at least one Supplier instance"
    assert all(o["_objectType"] == "Supplier" for o in objects), objects
    assert all("country" in o for o in objects), objects


def test_self_serve_implementer_is_included_in_interface_objects(
    msmith_token: str, jdoe_token: str
) -> None:
    """P0a: polymorphic list must use list_object_types + _type_handle,."""
    reviews_api = "http://reviews-api:8000/reviews_with_tags.json"
    source_name = _unique_name("iface_reviews")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": source_name, "base_url": reviews_api},
    )
    assert status == 200, registration
    status, sync = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, sync

    object_type_name = _unique_name("ReviewDoc")
    status, _ = _request(
        "POST", holon_url("/object-types"), token=msmith_token,
        body={
            "name": object_type_name,
            "source_dataset_urn": sync["dataset_urn"],
            "property_mapping": {"id": "id", "comment": "comment"},
            "description": "self-serve OT for interface polymorphic list",
        },
    )
    assert status == 201

    interface_name = _unique_name("HasComment")
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": interface_name, "required_properties": ["comment"]},
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions"), token=msmith_token,
        body={"implements": [interface_name]},
    )
    assert status == 201, draft
    status, published = _request(
        "POST",
        ontology_url(f"/objectTypes/{object_type_name}/versions/{draft['version']}/publish"),
        token=msmith_token,
    )
    assert status == 200, published

    status, objects = _request(
        "GET", ontology_url(f"/interfaceTypes/{interface_name}/objects"), token=jdoe_token
    )
    assert status == 200, objects
    assert objects, "expected at least one self-serve instance via interface"
    assert all(o["_objectType"] == object_type_name for o in objects), objects
    assert all("comment" in o for o in objects), objects


def test_objects_endpoint_404s_for_an_unknown_interface(jdoe_token: str) -> None:
    status, body = _request(
        "GET", ontology_url(f"/interfaceTypes/{_unique_name('NeverRegistered')}/objects"), token=jdoe_token
    )
    assert status == 404, body


def test_declarative_action_satisfies_required_actions_at_publish(msmith_token: str) -> None:
    """`_validate_implements` must see declarative ActionTypes, not only."""
    local_action = _unique_name("flag")
    action_name = f"Supplier.{local_action}"
    status, _ = _request(
        "POST", ontology_url("/actionTypes"), token=msmith_token,
        body={
            "name": action_name,
            "target_object_type": "Supplier",
            "required_permission": "write",
            "risk_level": "low",
            "description": "declarative action used as an interface required_action",
            "parameters": [],
            "edits": [{"property": "country", "source": "literal", "value": "FR"}],
        },
    )
    assert status == 201

    interface_name = _unique_name("HasDeclarativeFlag")
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": interface_name, "required_actions": [local_action]},
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url("/objectTypes/Supplier/versions"), token=msmith_token,
        body={"implements": [interface_name]},
    )
    assert status == 201, draft

    status, published = _request(
        "POST", ontology_url(f"/objectTypes/Supplier/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 200, published
    assert interface_name in (published.get("implements") or []), published


def test_interface_targeted_action_satisfies_required_actions_at_publish(msmith_token: str) -> None:
    """Actions-on-interfaces: an ActionType with `target_interface` must."""
    interface_name = _unique_name("Holdable")
    local_action = _unique_name("hold")
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": interface_name, "required_actions": [local_action]},
    )
    assert status == 201

    status, _ = _request(
        "POST", ontology_url("/actionTypes"), token=msmith_token,
        body={
            "name": f"{interface_name}.{local_action}",
            "target_interface": interface_name,
            "required_permission": "write",
            "risk_level": "low",
            "description": "interface-scoped action covering required_actions",
            "parameters": [],
            "edits": [{"property": "country", "source": "literal", "value": "FR"}],
        },
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url("/objectTypes/Supplier/versions"), token=msmith_token,
        body={"implements": [interface_name]},
    )
    assert status == 201, draft

    status, published = _request(
        "POST", ontology_url(f"/objectTypes/Supplier/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 200, published
    assert interface_name in (published.get("implements") or []), published


def test_tighten_interface_blocked_when_implementer_would_break(msmith_token: str) -> None:
    """P0c: adding a required property that a published implementer lacks."""
    interface_name = _unique_name("LooseThenTight")
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": interface_name, "required_properties": ["country"]},
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url("/objectTypes/Supplier/versions"), token=msmith_token,
        body={"implements": [interface_name]},
    )
    assert status == 201, draft
    status, published = _request(
        "POST",
        ontology_url(f"/objectTypes/Supplier/versions/{draft['version']}/publish"),
        token=msmith_token,
    )
    assert status == 200, published

    status, body = _request(
        "PUT", ontology_url(f"/interfaceTypes/{interface_name}"), token=msmith_token,
        body={"required_properties": ["country", "thisPropertyDoesNotExistOnSupplier"]},
    )
    assert status == 400, body
    detail = body.get("detail", "")
    assert "cannot tighten" in detail, body
    assert "Supplier" in detail, body


def test_relax_or_compatible_tighten_allowed(msmith_token: str) -> None:
    """Removing a requirement always works; adding one the implementer."""
    interface_name = _unique_name("CanRelax")
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": interface_name, "required_properties": ["country", "name"]},
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url("/objectTypes/Supplier/versions"), token=msmith_token,
        body={"implements": [interface_name]},
    )
    assert status == 201, draft
    status, published = _request(
        "POST",
        ontology_url(f"/objectTypes/Supplier/versions/{draft['version']}/publish"),
        token=msmith_token,
    )
    assert status == 200, published

    status, relaxed = _request(
        "PUT", ontology_url(f"/interfaceTypes/{interface_name}"), token=msmith_token,
        body={"required_properties": ["country"]},
    )
    assert status == 200, relaxed
    assert relaxed["required_properties"] == ["country"], relaxed

    status, retightened = _request(
        "PUT", ontology_url(f"/interfaceTypes/{interface_name}"), token=msmith_token,
        body={"required_properties": ["country", "name"]},
    )
    assert status == 200, retightened
    assert set(retightened["required_properties"]) == {"country", "name"}, retightened


def test_typed_interface_property_rejects_untyped_implementer(msmith_token: str) -> None:
    """P1a: interface property_types must match OT property_types at publish."""
    value_type_name = _unique_name("CountryCode")
    status, _ = _request(
        "POST", ontology_url("/valueTypes"), token=msmith_token,
        body={"name": value_type_name, "base_type": "string"},
    )
    assert status == 201

    interface_name = _unique_name("TypedCountry")
    status, created = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={
            "name": interface_name,
            "required_properties": ["country"],
            "property_types": {
                "country": {"kind": "value_type", "value_type": value_type_name},
            },
        },
    )
    assert status == 201, created
    assert created["property_types"]["country"]["value_type"] == value_type_name

    status, draft = _request(
        "POST", ontology_url("/objectTypes/Supplier/versions"), token=msmith_token,
        body={"implements": [interface_name]},
    )
    assert status == 201, draft
    status, publish_result = _request(
        "POST",
        ontology_url(f"/objectTypes/Supplier/versions/{draft['version']}/publish"),
        token=msmith_token,
    )
    assert status == 400, publish_result
    assert "must be typed" in publish_result["detail"], publish_result

    status, typed_draft = _request(
        "POST", ontology_url("/objectTypes/Supplier/versions"), token=msmith_token,
        body={
            "implements": [interface_name],
            "property_types": {
                "country": {"kind": "value_type", "value_type": value_type_name},
            },
        },
    )
    assert status == 201, typed_draft
    status, published = _request(
        "POST",
        ontology_url(f"/objectTypes/Supplier/versions/{typed_draft['version']}/publish"),
        token=msmith_token,
    )
    assert status == 200, published


def test_typed_interface_rejects_unknown_value_type(msmith_token: str) -> None:
    interface_name = _unique_name("BadTyped")
    status, body = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={
            "name": interface_name,
            "required_properties": ["country"],
            "property_types": {
                "country": {"kind": "value_type", "value_type": _unique_name("MissingVT")},
            },
        },
    )
    assert status == 400, body
    assert "unknown value_type" in body["detail"], body


def test_link_constraint_requires_binding_at_publish(msmith_token: str) -> None:
    """P1b: required interface link constraint must bind a RelationType."""
    interface_name = _unique_name("HasCustomer")
    status, created = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={
            "name": interface_name,
            "link_constraints": [
                {
                    "api_name": "customer",
                    "target_kind": "object_type",
                    "target": "Customer",
                    "cardinality": "one",
                    "required": True,
                }
            ],
        },
    )
    assert status == 201, created
    assert created["link_constraints"][0]["api_name"] == "customer"

    status, draft = _request(
        "POST", ontology_url("/objectTypes/Order/versions"), token=msmith_token,
        body={"implements": [interface_name]},
    )
    assert status == 201, draft
    status, publish_result = _request(
        "POST",
        ontology_url(f"/objectTypes/Order/versions/{draft['version']}/publish"),
        token=msmith_token,
    )
    assert status == 400, publish_result
    assert "missing required link binding" in publish_result["detail"], publish_result

    status, bound = _request(
        "POST", ontology_url("/objectTypes/Order/versions"), token=msmith_token,
        body={
            "implements": [interface_name],
            "link_constraint_bindings": {interface_name: {"customer": "Order.customer"}},
        },
    )
    assert status == 201, bound
    status, published = _request(
        "POST",
        ontology_url(f"/objectTypes/Order/versions/{bound['version']}/publish"),
        token=msmith_token,
    )
    assert status == 200, published
    assert published.get("link_constraint_bindings", {}).get(interface_name, {}).get("customer") == "Order.customer"


def test_link_constraint_rejects_wrong_cardinality(msmith_token: str) -> None:
    interface_name = _unique_name("ManyCustomers")
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={
            "name": interface_name,
            "link_constraints": [
                {
                    "api_name": "customer",
                    "target_kind": "object_type",
                    "target": "Customer",
                    "cardinality": "many",
                    "required": True,
                }
            ],
        },
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url("/objectTypes/Order/versions"), token=msmith_token,
        body={
            "implements": [interface_name],
            "link_constraint_bindings": {interface_name: {"customer": "Order.customer"}},
        },
    )
    assert status == 201, draft
    status, publish_result = _request(
        "POST",
        ontology_url(f"/objectTypes/Order/versions/{draft['version']}/publish"),
        token=msmith_token,
    )
    assert status == 400, publish_result
    assert "cardinality" in publish_result["detail"], publish_result


def test_child_interface_inherits_parent_required_property(msmith_token: str) -> None:
    """P1c: implementing Child must satisfy Parent's required properties."""
    parent = _unique_name("Contactable")
    child = _unique_name("Phoneable")
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": parent, "required_properties": ["country"]},
    )
    assert status == 201
    status, created = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": child, "parent_interfaces": [parent], "required_properties": []},
    )
    assert status == 201, created
    assert created["parent_interfaces"] == [parent], created

    status, draft = _request(
        "POST", ontology_url("/objectTypes/Order/versions"), token=msmith_token,
        body={"implements": [child]},
    )
    assert status == 201, draft
    status, publish_result = _request(
        "POST",
        ontology_url(f"/objectTypes/Order/versions/{draft['version']}/publish"),
        token=msmith_token,
    )
    assert status == 400, publish_result
    assert "missing required property" in publish_result["detail"], publish_result
    assert "country" in publish_result["detail"], publish_result

    status, ok_draft = _request(
        "POST", ontology_url("/objectTypes/Supplier/versions"), token=msmith_token,
        body={"implements": [child]},
    )
    assert status == 201, ok_draft
    status, published = _request(
        "POST",
        ontology_url(f"/objectTypes/Supplier/versions/{ok_draft['version']}/publish"),
        token=msmith_token,
    )
    assert status == 200, published
    assert child in (published.get("implements") or []), published


def test_parent_interfaces_rejects_cycle(msmith_token: str) -> None:
    a = _unique_name("CycleA")
    b = _unique_name("CycleB")
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": a, "required_properties": []},
    )
    assert status == 201
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": b, "parent_interfaces": [a]},
    )
    assert status == 201

    status, body = _request(
        "PUT", ontology_url(f"/interfaceTypes/{a}"), token=msmith_token,
        body={"parent_interfaces": [b]},
    )
    assert status == 400, body
    assert "cycle" in body["detail"], body


def test_parent_objects_includes_child_implementer(
    msmith_token: str, jdoe_token: str
) -> None:
    """P1c: GET Parent/objects expands through Child implementers."""
    parent = _unique_name("HasCountryParent")
    child = _unique_name("HasCountryChild")
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": parent, "required_properties": ["country"]},
    )
    assert status == 201
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": child, "parent_interfaces": [parent]},
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url("/objectTypes/Supplier/versions"), token=msmith_token,
        body={"implements": [child]},
    )
    assert status == 201, draft
    status, published = _request(
        "POST",
        ontology_url(f"/objectTypes/Supplier/versions/{draft['version']}/publish"),
        token=msmith_token,
    )
    assert status == 200, published

    status, objects = _request(
        "GET", ontology_url(f"/interfaceTypes/{parent}/objects"), token=jdoe_token
    )
    assert status == 200, objects
    assert any(o.get("_objectType") == "Supplier" for o in objects), objects


def test_tighten_parent_blocked_by_child_implementer(msmith_token: str) -> None:
    """P1c: tightening Parent must re-validate OTs that only implement Child."""
    parent = _unique_name("LooseParent")
    child = _unique_name("LooseChild")
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": parent, "required_properties": ["country"]},
    )
    assert status == 201
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": child, "parent_interfaces": [parent]},
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url("/objectTypes/Supplier/versions"), token=msmith_token,
        body={"implements": [child]},
    )
    assert status == 201, draft
    status, published = _request(
        "POST",
        ontology_url(f"/objectTypes/Supplier/versions/{draft['version']}/publish"),
        token=msmith_token,
    )
    assert status == 200, published

    status, body = _request(
        "PUT", ontology_url(f"/interfaceTypes/{parent}"), token=msmith_token,
        body={"required_properties": ["country", "thisPropertyDoesNotExistOnSupplier"]},
    )
    assert status == 400, body
    detail = body.get("detail", "")
    assert "cannot tighten" in detail, body
    assert "Supplier" in detail, body


def test_search_interface_filter_narrows_to_implementers(msmith_token: str, jdoe_token: str) -> None:
    """P1d: GET /search?interface=… post-filters to OTs whose implements expand."""
    status, sync = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "customers"})
    assert status == 200, sync

    parent = _unique_name("SearchableParent")
    child = _unique_name("SearchableChild")
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": parent, "required_properties": ["name"]},
    )
    assert status == 201
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": child, "parent_interfaces": [parent]},
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url("/objectTypes/Customer/versions"), token=msmith_token,
        body={"implements": [child]},
    )
    assert status == 201, draft
    status, published = _request(
        "POST",
        ontology_url(f"/objectTypes/Customer/versions/{draft['version']}/publish"),
        token=msmith_token,
    )
    assert status == 200, published

    deadline = time.monotonic() + 30
    filtered = None
    while time.monotonic() < deadline:
        status, filtered = _request(
            "GET", holon_url(f"/search?q=Acme&interface={parent}&size=50"), token=jdoe_token,
        )
        assert status == 200, filtered
        if filtered.get("results"):
            break
        time.sleep(1.5)
    assert filtered and filtered["results"], filtered
    assert all(r["object_type"] == "Customer" for r in filtered["results"]), filtered

    status, missing = _request(
        "GET", holon_url(f"/search?q=Acme&interface={_unique_name('NoSuchIface')}"), token=jdoe_token,
    )
    assert status == 404, missing


def test_delete_interface_blocked_by_implementer_and_succeeds_when_clear(msmith_token: str) -> None:
    """P2: DELETE refuses published implementers; succeeds after un-implement."""
    iface = _unique_name("DeletableIface")
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": iface, "required_properties": ["country"]},
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url("/objectTypes/Supplier/versions"), token=msmith_token,
        body={"implements": [iface]},
    )
    assert status == 201, draft
    status, published = _request(
        "POST",
        ontology_url(f"/objectTypes/Supplier/versions/{draft['version']}/publish"),
        token=msmith_token,
    )
    assert status == 200, published

    status, body = _request("DELETE", ontology_url(f"/interfaceTypes/{iface}"), token=msmith_token)
    assert status == 400, body
    assert "implementer" in body["detail"], body

    status, clear = _request(
        "POST", ontology_url("/objectTypes/Supplier/versions"), token=msmith_token,
        body={"implements": []},
    )
    assert status == 201, clear
    status, cleared = _request(
        "POST",
        ontology_url(f"/objectTypes/Supplier/versions/{clear['version']}/publish"),
        token=msmith_token,
    )
    assert status == 200, cleared

    status, deleted = _request("DELETE", ontology_url(f"/interfaceTypes/{iface}"), token=msmith_token)
    assert status == 200, deleted
    assert deleted["name"] == iface, deleted
    status, missing = _request("GET", ontology_url(f"/interfaceTypes/{iface}"), token=msmith_token)
    assert status == 404, missing


def test_struct_field_binding_satisfies_interface_property(msmith_token: str, jdoe_token: str) -> None:
    """P2c: interface required prop can bind to a one-level struct field path."""
    reviews_api = "http://reviews-api:8000/reviews_with_tags.json"
    source_name = _unique_name("struct_iface")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": source_name, "base_url": reviews_api},
    )
    assert status == 200, registration
    status, sync = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, sync

    vt = _unique_name("CityString")
    status, _ = _request(
        "POST", ontology_url("/valueTypes"), token=msmith_token,
        body={"name": vt, "base_type": "string"},
    )
    assert status == 201

    object_type_name = _unique_name("StructDoc")
    status, _ = _request(
        "POST", holon_url("/object-types"), token=msmith_token,
        body={
            "name": object_type_name,
            "source_dataset_urn": sync["dataset_urn"],
            "property_mapping": {"id": "id", "address": "comment"},
            "description": "struct-field interface binding",
        },
    )
    assert status == 201

    iface = _unique_name("HasCity")
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": iface, "required_properties": ["city"]},
    )
    assert status == 201

    status, missing = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions"), token=msmith_token,
        body={"implements": [iface]},
    )
    assert status == 201, missing
    status, publish_missing = _request(
        "POST",
        ontology_url(f"/objectTypes/{object_type_name}/versions/{missing['version']}/publish"),
        token=msmith_token,
    )
    assert status == 400, publish_missing
    assert "missing required property" in publish_missing["detail"], publish_missing

    status, bound = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions"), token=msmith_token,
        body={
            "implements": [iface],
            "property_types": {
                "address": {
                    "kind": "struct",
                    "properties": {"city": {"kind": "value_type", "value_type": vt}},
                }
            },
            "interface_property_bindings": {iface: {"city": "address.city"}},
        },
    )
    assert status == 201, bound
    status, published = _request(
        "POST",
        ontology_url(f"/objectTypes/{object_type_name}/versions/{bound['version']}/publish"),
        token=msmith_token,
    )
    assert status == 200, published
    assert published.get("interface_property_bindings", {}).get(iface, {}).get("city") == "address.city"
