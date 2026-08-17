"""Tests for Shared Property Types."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import CONNECTIVITY, KNOWLEDGE, _request, _unique_name, ontology_url, holon_url

REVIEWS_WITH_TAGS_API = "http://reviews-api:8000/reviews_with_tags.json"


def _register_value_type(msmith_token: str, *, base_type: str = "string") -> str:
    name = _unique_name("SptEmail")
    status, created = _request(
        "POST", ontology_url("/valueTypes"), token=msmith_token, body={"name": name, "base_type": base_type}
    )
    assert status == 201, created
    return name


def test_editor_cannot_create_a_shared_property_type(jdoe_token: str, msmith_token: str) -> None:
    value_type_name = _register_value_type(msmith_token)
    status, body = _request(
        "POST", ontology_url("/sharedPropertyTypes"), token=jdoe_token,
        body={"api_name": _unique_name("denied"), "display_name": "Denied", "value_type": value_type_name},
    )
    assert status == 403, body


def test_creating_a_shared_property_type_with_an_unknown_value_type_is_400(msmith_token: str) -> None:
    status, body = _request(
        "POST", ontology_url("/sharedPropertyTypes"), token=msmith_token,
        body={"api_name": _unique_name("bogus"), "display_name": "Bogus", "value_type": _unique_name("never_registered")},
    )
    assert status == 400, body
    assert "unknown value_type" in body["detail"], body


def test_register_list_and_reject_duplicate_shared_property_type(msmith_token: str) -> None:
    value_type_name = _register_value_type(msmith_token)
    api_name = _unique_name("email")
    status, created = _request(
        "POST", ontology_url("/sharedPropertyTypes"), token=msmith_token,
        body={
            "api_name": api_name, "display_name": "Email address", "value_type": value_type_name,
            "description": "the canonical contact email property",
        },
    )
    assert status == 201, created
    assert created["value_type"] == value_type_name, created
    assert created["display_name"] == "Email address", created

    status, listed = _request("GET", ontology_url("/sharedPropertyTypes"), token=msmith_token)
    assert status == 200, listed
    assert any(spt["api_name"] == api_name for spt in listed), listed

    status, fetched = _request("GET", ontology_url(f"/sharedPropertyTypes/{api_name}"), token=msmith_token)
    assert status == 200 and fetched["api_name"] == api_name, fetched

    status, dupe = _request(
        "POST", ontology_url("/sharedPropertyTypes"), token=msmith_token,
        body={"api_name": api_name, "display_name": "Email address (again)", "value_type": value_type_name},
    )
    assert status == 409, dupe


def test_unknown_shared_property_type_is_404(msmith_token: str) -> None:
    status, body = _request(
        "GET", ontology_url(f"/sharedPropertyTypes/{_unique_name('never_registered')}"), token=msmith_token
    )
    assert status == 404, body


def _register_sync_and_create_object_type(msmith_token: str, jdoe_token: str) -> str:
    source_name = _unique_name("spt_reviews")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": source_name, "base_url": REVIEWS_WITH_TAGS_API},
    )
    assert status == 200, registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, result

    object_type_name = _unique_name("SptReview")
    status, object_type = _request(
        "POST", holon_url("/object-types"), token=msmith_token,
        body={
            "name": object_type_name,
            "source_dataset_urn": result["dataset_urn"],
            "property_mapping": {"id": "id", "comment": "comment"},
            "description": "pytest-created",
        },
    )
    assert status == 201, object_type
    return object_type_name


def test_a_shared_property_type_can_type_a_real_property_end_to_end(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    value_type_name = _register_value_type(msmith_token)
    api_name = _unique_name("comment_field")
    status, _ = _request(
        "POST", ontology_url("/sharedPropertyTypes"), token=msmith_token,
        body={"api_name": api_name, "display_name": "Comment", "value_type": value_type_name},
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions"), token=msmith_token,
        body={"property_types": {"comment": {"kind": "shared_property_type", "shared_property_type": api_name}}},
    )
    assert status == 201, draft

    status, published = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 200, published
    assert published["property_types"]["comment"]["kind"] == "shared_property_type", published
    assert published["property_types"]["comment"]["shared_property_type"] == api_name, published

    status, fetched = _request("GET", ontology_url(f"/objectTypes/{object_type_name}"), token=msmith_token)
    assert status == 200, fetched
    assert fetched["property_types"]["comment"]["shared_property_type"] == api_name, fetched


def test_property_types_referencing_an_unknown_shared_property_type_is_rejected_at_publish(
    msmith_token: str, jdoe_token: str
) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    status, draft = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions"), token=msmith_token,
        body={"property_types": {"comment": {"kind": "shared_property_type", "shared_property_type": _unique_name("never_registered")}}},
    )
    assert status == 201, draft

    status, result = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 400, result
    assert "unknown shared_property_type" in result["detail"], result


def test_a_shared_property_type_can_be_used_inside_a_struct_leaf(msmith_token: str, jdoe_token: str) -> None:
    """One-level nesting applies the same way it does to a bare."""
    source_name = _unique_name("spt_struct_reviews")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": source_name, "base_url": REVIEWS_WITH_TAGS_API},
    )
    assert status == 200, registration
    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, result

    object_type_name = _unique_name("SptStructReview")
    status, object_type = _request(
        "POST", holon_url("/object-types"), token=msmith_token,
        body={
            "name": object_type_name, "source_dataset_urn": result["dataset_urn"],
            "property_mapping": {"id": "id", "comment": "comment", "address": "address_json"},
            "description": "pytest-created",
        },
    )
    assert status == 201, object_type

    value_type_name = _register_value_type(msmith_token)
    api_name = _unique_name("city_field")
    status, _ = _request(
        "POST", ontology_url("/sharedPropertyTypes"), token=msmith_token,
        body={"api_name": api_name, "display_name": "City", "value_type": value_type_name},
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions"), token=msmith_token,
        body={"property_types": {
            "address": {"kind": "struct", "properties": {"city": {"kind": "shared_property_type", "shared_property_type": api_name}}},
        }},
    )
    assert status == 201, draft

    status, published = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 200, published
