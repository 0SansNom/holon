"""Tests for Value Types."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import CONNECTIVITY, KNOWLEDGE, _request, _unique_name, ontology_url, holon_url, resync_and_wait_for_instance

REVIEWS_WITH_TAGS_API = "http://reviews-api:8000/reviews_with_tags.json"


def test_editor_cannot_create_a_value_type(jdoe_token: str) -> None:
    status, body = _request(
        "POST", ontology_url("/valueTypes"), token=jdoe_token,
        body={"name": _unique_name("Denied"), "base_type": "string"},
    )
    assert status == 403, body


def test_creating_a_value_type_with_an_unknown_base_type_is_400(msmith_token: str) -> None:
    status, body = _request(
        "POST", ontology_url("/valueTypes"), token=msmith_token,
        body={"name": _unique_name("Bogus"), "base_type": "wat"},
    )
    assert status == 400, body
    assert "base_type" in body["detail"], body


def test_a_format_regex_on_a_non_string_base_type_is_400(msmith_token: str) -> None:
    status, body = _request(
        "POST", ontology_url("/valueTypes"), token=msmith_token,
        body={"name": _unique_name("BadRegexTarget"), "base_type": "integer", "format_regex": "^[0-9]+$"},
    )
    assert status == 400, body
    assert "format_regex" in body["detail"], body


def test_register_list_and_reject_duplicate_value_type(msmith_token: str) -> None:
    name = _unique_name("Email")
    status, created = _request(
        "POST", ontology_url("/valueTypes"), token=msmith_token,
        body={"name": name, "base_type": "string", "format_regex": r"^[^@]+@[^@]+\.[^@]+$", "description": "an email"},
    )
    assert status == 201, created
    assert created["base_type"] == "string", created

    status, listed = _request("GET", ontology_url("/valueTypes"), token=msmith_token)
    assert status == 200, listed
    assert any(v["name"] == name for v in listed), listed

    status, fetched = _request("GET", ontology_url(f"/valueTypes/{name}"), token=msmith_token)
    assert status == 200 and fetched["name"] == name, fetched

    status, dupe = _request(
        "POST", ontology_url("/valueTypes"), token=msmith_token, body={"name": name, "base_type": "string"}
    )
    assert status == 409, dupe


def test_unknown_value_type_is_404(msmith_token: str) -> None:
    status, body = _request("GET", ontology_url(f"/valueTypes/{_unique_name('never_registered')}"), token=msmith_token)
    assert status == 404, body


def _register_sync_and_create_object_type(msmith_token: str, jdoe_token: str) -> str:
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
        "POST", holon_url("/object-types"), token=msmith_token,
        body={
            "name": object_type_name,
            "source_dataset_urn": result["dataset_urn"],
            "property_mapping": {"id": "id", "comment": "comment", "tags": "tags_json", "address": "address_json"},
            "description": "pytest-created",
        },
    )
    assert status == 201, object_type
    resync_and_wait_for_instance(token=jdoe_token, dataset=source_name, object_type=object_type_name)
    return object_type_name


def test_a_self_serve_object_type_can_now_be_versioned(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    status, draft = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions"), token=msmith_token,
        body={"description": "a self-serve type can now be versioned"},
    )
    assert status == 201, draft
    assert draft["version"] == 2, draft


def test_struct_and_array_property_types_are_validated_and_parsed_at_read_time(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    value_type_name = _unique_name("City")
    status, _ = _request(
        "POST", ontology_url("/valueTypes"), token=msmith_token, body={"name": value_type_name, "base_type": "string"}
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions"), token=msmith_token,
        body={
            "property_types": {
                "tags": {"kind": "array", "element": {"kind": "value_type", "value_type": value_type_name}},
                "address": {"kind": "struct", "properties": {"city": {"kind": "value_type", "value_type": value_type_name}}},
            }
        },
    )
    assert status == 201, draft

    status, published = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 200, published
    assert published["property_types"]["tags"]["kind"] == "array", published

    status, instance = _request("GET", ontology_url(f"/objects/{object_type_name}/1"), token=msmith_token)
    assert status == 200, instance
    # Parsed nested dict projected to the declared struct shape.
    assert instance["tags_json"] == ["quality", "integration"], instance
    assert instance["address_json"] == {"city": "Lyon"}, instance


def test_property_types_referencing_an_unknown_value_type_is_rejected_at_publish(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    status, draft = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions"), token=msmith_token,
        body={"property_types": {"comment": {"kind": "value_type", "value_type": _unique_name("never_registered")}}},
    )
    assert status == 201, draft

    status, result = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 400, result
    assert "unknown value_type" in result["detail"], result


def test_property_types_naming_an_unknown_property_is_rejected_at_publish(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    value_type_name = _unique_name("Whatever")
    status, _ = _request(
        "POST", ontology_url("/valueTypes"), token=msmith_token, body={"name": value_type_name, "base_type": "string"}
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions"), token=msmith_token,
        body={"property_types": {"nonexistentProperty": {"kind": "value_type", "value_type": value_type_name}}},
    )
    assert status == 201, draft

    status, result = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 400, result
    assert "doesn't have" in result["detail"], result


def test_value_type_metadata_and_version_bump(msmith_token: str) -> None:
    """Constraint/format changes bump version and archive revisions;."""
    name = _unique_name("EmailVT")
    status, created = _request(
        "POST",
        ontology_url("/valueTypes"),
        token=msmith_token,
        body={
            "name": name,
            "base_type": "string",
            "format_regex": r"^[^@]+@[^@]+\.[^@]+$",
            "api_name": "EmailAddress",
            "display_name": "Email address",
            "example_value": "a@b.co",
            "description": "an email",
        },
    )
    assert status == 201, created
    assert created["version"] == 1, created
    assert created["api_name"] == "EmailAddress", created
    assert created["display_name"] == "Email address", created
    assert created["example_value"] == "a@b.co", created
    assert created["lifecycle_status"] == "experimental", created

    status, meta_only = _request(
        "PUT",
        ontology_url(f"/valueTypes/{name}"),
        token=msmith_token,
        body={"description": "updated blurb", "example_value": "x@y.z"},
    )
    assert status == 200, meta_only
    assert meta_only["version"] == 1, meta_only
    assert meta_only["description"] == "updated blurb", meta_only
    assert meta_only["example_value"] == "x@y.z", meta_only

    status, bumped = _request(
        "PUT",
        ontology_url(f"/valueTypes/{name}"),
        token=msmith_token,
        body={"format_regex": r"^[^@]+@example\.com$"},
    )
    assert status == 200, bumped
    assert bumped["version"] == 2, bumped
    assert bumped["format_regex"] == r"^[^@]+@example\.com$", bumped

    status, revisions = _request(
        "GET", ontology_url(f"/valueTypes/{name}/revisions"), token=msmith_token
    )
    assert status == 200, revisions
    versions = {r["version"] for r in revisions}
    assert versions == {1, 2}, revisions

    status, deprecated = _request(
        "POST",
        ontology_url(f"/valueTypes/{name}/deprecate"),
        token=msmith_token,
        body={"deprecation_reason": "superseded by a newer format", "deprecation_deadline": "2027-01-01"},
    )
    assert status == 200, deprecated
    assert deprecated["lifecycle_status"] == "deprecated", deprecated
    assert deprecated["version"] == 2, deprecated

    status, active_only = _request(
        "GET", ontology_url("/valueTypes?include_deprecated=false"), token=msmith_token
    )
    assert status == 200, active_only
    assert not any(v["name"] == name for v in active_only), active_only


def test_validate_casts_endpoint_reports_row_errors(msmith_token: str) -> None:
    name = _unique_name("CastEmail")
    status, created = _request(
        "POST",
        ontology_url("/valueTypes"),
        token=msmith_token,
        body={"name": name, "base_type": "string", "format_regex": r"^[^@]+@[^@]+\.[^@]+$"},
    )
    assert status == 201, created

    status, result = _request(
        "POST",
        ontology_url("/valueTypes/validate-casts"),
        token=msmith_token,
        body={
            "casts": {"email": name},
            "rows": [{"email": "ok@example.com"}, {"email": "not-an-email"}],
        },
    )
    assert status == 200, result
    assert result["ok"] is False, result
    assert result["error_count"] == 1, result
    assert result["errors"][0]["row_index"] == 1, result


def test_substring_regex_and_permissions_endpoint(msmith_token: str) -> None:
    name = _unique_name("ContainsAt")
    status, created = _request(
        "POST",
        ontology_url("/valueTypes"),
        token=msmith_token,
        body={
            "name": name,
            "base_type": "string",
            "format_regex": r"@",
            "format_regex_match": "substring",
        },
    )
    assert status == 201, created
    assert created["format_regex_match"] == "substring", created
    assert created.get("urn"), created

    status, bumped = _request(
        "PUT",
        ontology_url(f"/valueTypes/{name}"),
        token=msmith_token,
        body={"format_regex_match": "full"},
    )
    assert status == 200, bumped
    assert bumped["version"] == 2, bumped

    status, perms = _request(
        "GET", ontology_url(f"/valueTypes/{name}/permissions"), token=msmith_token
    )
    assert status == 200, perms
    assert perms["permissions"]["read"] is True, perms
    assert perms["urn"] == created["urn"], perms


def test_struct_and_array_nesting_is_limited_to_one_level(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    value_type_name = _unique_name("Leaf")
    status, _ = _request(
        "POST", ontology_url("/valueTypes"), token=msmith_token, body={"name": value_type_name, "base_type": "string"}
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions"), token=msmith_token,
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
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 400, result
    assert "one level" in result["detail"], result
