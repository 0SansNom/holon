"""Value Types + typed properties — domain primitive and structural property typing:
`property_mapping` was a flat `{name: source_column}`
dict with no data-type vocabulary at all. A genuinely separate concern
from the pre-existing `property_formats` (display formatting only,
`test_ontology_lifecycle.py`'s neighborhood) — `value_type` is a real,
named, reusable *data* type (base primitive + optional format regex),
and `property_types` declares a property's shape (a typed leaf, or one
level of `struct`/`array` nesting), validated at publish time and
rendered for real at read time (a struct/array property's JSON-text
source column is parsed into an actual nested dict/list, not left as an
opaque string).

Also exercises an adjacent fix: the ontology-lifecycle endpoints
(`propose`/`publish`/branches) only ever resolved a static six-seeded-
type dict, so a self-serve ObjectType (`POST /object-types`) — the most
natural audience for typed struct/array properties, since it's
JSON-native — could never be versioned at all. Fixed by routing through
the same self-serve-aware URN fallback `GET /ontology/{name}` already
used. No real LLM calls.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import CONNECTIVITY, KNOWLEDGE, _request, _unique_name

REVIEWS_WITH_TAGS_API = "http://reviews-api:8000/reviews_with_tags.json"


def test_editor_cannot_create_a_value_type(jdoe_token: str) -> None:
    status, body = _request(
        "POST", f"{KNOWLEDGE}/value-types", token=jdoe_token,
        body={"name": _unique_name("Denied"), "base_type": "string"},
    )
    assert status == 403, body


def test_creating_a_value_type_with_an_unknown_base_type_is_400(msmith_token: str) -> None:
    status, body = _request(
        "POST", f"{KNOWLEDGE}/value-types", token=msmith_token,
        body={"name": _unique_name("Bogus"), "base_type": "wat"},
    )
    assert status == 400, body
    assert "base_type" in body["detail"], body


def test_a_format_regex_on_a_non_string_base_type_is_400(msmith_token: str) -> None:
    status, body = _request(
        "POST", f"{KNOWLEDGE}/value-types", token=msmith_token,
        body={"name": _unique_name("BadRegexTarget"), "base_type": "integer", "format_regex": "^[0-9]+$"},
    )
    assert status == 400, body
    assert "format_regex" in body["detail"], body


def test_register_list_and_reject_duplicate_value_type(msmith_token: str) -> None:
    name = _unique_name("Email")
    status, created = _request(
        "POST", f"{KNOWLEDGE}/value-types", token=msmith_token,
        body={"name": name, "base_type": "string", "format_regex": r"^[^@]+@[^@]+\.[^@]+$", "description": "an email"},
    )
    assert status == 201, created
    assert created["base_type"] == "string", created

    status, listed = _request("GET", f"{KNOWLEDGE}/value-types", token=msmith_token)
    assert status == 200, listed
    assert any(v["name"] == name for v in listed), listed

    status, fetched = _request("GET", f"{KNOWLEDGE}/value-types/{name}", token=msmith_token)
    assert status == 200 and fetched["name"] == name, fetched

    status, dupe = _request(
        "POST", f"{KNOWLEDGE}/value-types", token=msmith_token, body={"name": name, "base_type": "string"}
    )
    assert status == 409, dupe


def test_unknown_value_type_is_404(msmith_token: str) -> None:
    status, body = _request("GET", f"{KNOWLEDGE}/value-types/{_unique_name('never_registered')}", token=msmith_token)
    assert status == 404, body


def _register_sync_and_create_object_type(msmith_token: str, jdoe_token: str) -> str:
    """Shared setup for the struct/array read-path tests below: a
    self-serve ObjectType backed by real JSON-text columns
    (`tags_json`/`address_json`) — the natural fit for structured
    properties, since the no-code REST connector never flattens nested
    JSON. Returns the ObjectType name.
    """
    source_name = _unique_name("reviews_with_tags")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": source_name, "base_url": REVIEWS_WITH_TAGS_API},
    )
    assert status == 200, registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, result

    object_type_name = _unique_name("TaggedReview")
    status, object_type = _request(
        "POST", f"{KNOWLEDGE}/object-types", token=msmith_token,
        body={
            "name": object_type_name,
            "source_dataset_urn": result["dataset_urn"],
            "property_mapping": {"id": "id", "comment": "comment", "tags": "tags_json", "address": "address_json"},
            "description": "pytest-created, proves struct/array property_types against real data",
        },
    )
    assert status == 201, object_type
    return object_type_name


def test_a_self_serve_object_type_can_now_be_versioned(msmith_token: str, jdoe_token: str) -> None:
    """The adjacent bug: before the fix, `propose_object_type_version`
    only ever resolved the six boot-seeded types, so this 404'd for any
    self-serve ObjectType regardless of what was being proposed.
    """
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    status, draft = _request(
        "POST", f"{KNOWLEDGE}/ontology/{object_type_name}/versions", token=msmith_token,
        body={"description": "a self-serve type can now be versioned"},
    )
    assert status == 201, draft
    assert draft["version"] == 2, draft


def test_struct_and_array_property_types_are_validated_and_parsed_at_read_time(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    value_type_name = _unique_name("City")
    status, _ = _request(
        "POST", f"{KNOWLEDGE}/value-types", token=msmith_token, body={"name": value_type_name, "base_type": "string"}
    )
    assert status == 201

    status, draft = _request(
        "POST", f"{KNOWLEDGE}/ontology/{object_type_name}/versions", token=msmith_token,
        body={
            "property_types": {
                "tags": {"kind": "array", "element": {"kind": "value_type", "value_type": value_type_name}},
                "address": {"kind": "struct", "properties": {"city": {"kind": "value_type", "value_type": value_type_name}}},
            }
        },
    )
    assert status == 201, draft

    status, published = _request(
        "POST", f"{KNOWLEDGE}/ontology/{object_type_name}/versions/{draft['version']}/publish", token=msmith_token
    )
    assert status == 200, published
    assert published["property_types"]["tags"]["kind"] == "array", published

    status, instance = _request("GET", f"{KNOWLEDGE}/objects/{object_type_name}/1", token=msmith_token)
    assert status == 200, instance
    # The real proof: a genuine nested list/dict, not a raw JSON string.
    assert instance["tags_json"] == ["quality", "integration"], instance
    assert instance["address_json"] == {"city": "Lyon", "country": "FR"}, instance


def test_property_types_referencing_an_unknown_value_type_is_rejected_at_publish(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    status, draft = _request(
        "POST", f"{KNOWLEDGE}/ontology/{object_type_name}/versions", token=msmith_token,
        body={"property_types": {"comment": {"kind": "value_type", "value_type": _unique_name("never_registered")}}},
    )
    assert status == 201, draft

    status, result = _request(
        "POST", f"{KNOWLEDGE}/ontology/{object_type_name}/versions/{draft['version']}/publish", token=msmith_token
    )
    assert status == 400, result
    assert "unknown value_type" in result["detail"], result


def test_property_types_naming_an_unknown_property_is_rejected_at_publish(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    value_type_name = _unique_name("Whatever")
    status, _ = _request(
        "POST", f"{KNOWLEDGE}/value-types", token=msmith_token, body={"name": value_type_name, "base_type": "string"}
    )
    assert status == 201

    status, draft = _request(
        "POST", f"{KNOWLEDGE}/ontology/{object_type_name}/versions", token=msmith_token,
        body={"property_types": {"nonexistentProperty": {"kind": "value_type", "value_type": value_type_name}}},
    )
    assert status == 201, draft

    status, result = _request(
        "POST", f"{KNOWLEDGE}/ontology/{object_type_name}/versions/{draft['version']}/publish", token=msmith_token
    )
    assert status == 400, result
    assert "doesn't have" in result["detail"], result


def test_struct_and_array_nesting_is_limited_to_one_level(msmith_token: str, jdoe_token: str) -> None:
    """An explicit, stated scope boundary (see the plan) — a struct's own
    nested property, or an array's element, may only be a `value_type`
    leaf, never another `struct`/`array`.
    """
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    value_type_name = _unique_name("Leaf")
    status, _ = _request(
        "POST", f"{KNOWLEDGE}/value-types", token=msmith_token, body={"name": value_type_name, "base_type": "string"}
    )
    assert status == 201

    status, draft = _request(
        "POST", f"{KNOWLEDGE}/ontology/{object_type_name}/versions", token=msmith_token,
        body={
            "property_types": {
                "address": {
                    "kind": "struct",
                    "properties": {"nested": {"kind": "array", "element": {"kind": "value_type", "value_type": value_type_name}}},
                }
            }
        },
    )
    assert status == 201, draft

    status, result = _request(
        "POST", f"{KNOWLEDGE}/ontology/{object_type_name}/versions/{draft['version']}/publish", token=msmith_token
    )
    assert status == 400, result
    assert "one level" in result["detail"], result
