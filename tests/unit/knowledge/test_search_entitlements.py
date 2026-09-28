"""Unit tests for search document split and query filters (policy version 2)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "libs"))
sys.path.insert(0, str(REPO_ROOT / "services" / "knowledge"))

sys.modules.setdefault("httpx", types.ModuleType("httpx"))

from app.search import (  # noqa: E402
    POLICY_VERSION,
    build_search_document,
    build_search_query,
    present_search_hit,
    _marking_filter,
    _principal_marking_tokens,
    _rebac_object_type_filter,
    _required_marking_tokens,
)
from holon_common.auth import Principal  # noqa: E402

_MAPPING = {
    "name": "name",
    "email": "email",
    "lifetimeValue": "lifetime_value",
}
_CLASSIFICATIONS = {"email": "confidential", "lifetime_value": "confidential", "name": "internal"}


def _principal(country: str = "FR") -> Principal:
    return Principal(
        urn="hl:acme:global:user:jdoe",
        type="user",
        tenant_id="acme",
        display_name="Jane",
        country=country,
    )


def _document() -> dict:
    return build_search_document(
        object_type_name="Customer",
        tenant_id="acme",
        classification="confidential",
        property_mapping=_MAPPING,
        row={"id": "1", "name": "Acme Robotics", "email": "contact@acme-robotics.example", "lifetime_value": "184500.00"},
        property_types={
            "email": {"render_hints": ["searchable", "selectable"]},
            "lifetimeValue": {"render_hints": ["searchable", "selectable"]},
        },
        property_classifications=_CLASSIFICATIONS,
    )


def test_confidential_values_stay_out_of_public_text_and_props() -> None:
    document = _document()
    assert document["policy_version"] == POLICY_VERSION
    assert "Acme Robotics" in document["text"]
    assert "contact@acme-robotics.example" not in document["text"]
    assert "184500.00" not in document["text"]
    assert "contact@acme-robotics.example" in document["confidential_text"]
    assert "184500.00" in document["confidential_text"]
    assert "email" not in document.get("props", {})
    assert document["confidential_props"]["email"] == "contact@acme-robotics.example"


def test_unclassified_properties_inherit_a_confidential_object() -> None:
    document = build_search_document(
        object_type_name="Customer",
        tenant_id="acme",
        classification="confidential",
        property_mapping=_MAPPING,
        row={"id": "1", "name": "Acme Robotics", "email": "contact@acme-robotics.example", "lifetime_value": "184500.00"},
        property_types={
            "name": {"render_hints": ["searchable"]},
            "email": {"render_hints": ["searchable"]},
            "lifetimeValue": {"render_hints": ["searchable"]},
        },
        property_classifications={"name": "internal"},
    )
    assert "Acme Robotics" in document["text"]
    assert "contact@acme-robotics.example" not in document["text"]
    assert "184500.00" not in document["text"]
    assert "contact@acme-robotics.example" in document["confidential_text"]
    assert "184500.00" in document["confidential_text"]


def test_public_only_row_has_no_confidential_fields() -> None:
    document = build_search_document(
        object_type_name="ProductReview",
        tenant_id="acme",
        classification="public",
        property_mapping={"comment": "comment"},
        row={"id": "9", "comment": "Excellent"},
        property_classifications={"comment": "public"},
    )
    assert "Excellent" in document["text"]
    assert "confidential_text" not in document
    assert "confidential_props" not in document


def test_query_requires_policy_version_and_hides_confidential_text_by_default() -> None:
    query = build_search_query(principal=_principal("JP"), query_text="Acme", include_confidential=False)
    filters = query["query"]["bool"]["filter"]
    assert {"term": {"policy_version": POLICY_VERSION}} in filters
    assert {"term": {"tenant_id": "acme"}} in filters
    assert not any("entitlement_tokens" in str(clause) for clause in filters)
    assert query["query"]["bool"]["must"][0]["simple_query_string"]["fields"] == ["text"]


def test_cleared_query_also_searches_confidential_text() -> None:
    query = build_search_query(
        principal=_principal("FR"),
        query_text="contact@",
        include_confidential=True,
        property_filters={"email": "contact@acme-robotics.example"},
        selectable_props=["email"],
    )
    assert query["query"]["bool"]["must"][0]["simple_query_string"]["fields"] == ["text", "confidential_text"]
    post = query["post_filter"]
    assert {"term": {"confidential_props.email": "contact@acme-robotics.example"}} in post["bool"]["should"]
    assert "cprop_email" in query["aggs"]


def test_present_hit_strips_confidential_fields_unless_cleared() -> None:
    source = _document()
    hidden = present_search_hit(source, include_confidential=False)
    assert "confidential_text" not in hidden
    assert "contact@acme-robotics.example" not in hidden["text"]
    shown = present_search_hit(source, include_confidential=True)
    assert "contact@acme-robotics.example" in shown["text"]
    assert shown["props"]["email"] == "contact@acme-robotics.example"
    assert "confidential_text" not in shown


def test_rebac_filter_is_object_type_terms() -> None:
    clause = _rebac_object_type_filter(["Customer", "Order"])
    assert clause == {"terms": {"object_type": ["Customer", "Order"]}}


def test_required_marking_tokens_are_prefixed() -> None:
    assert _required_marking_tokens(["pii", "export"]) == ["mark:pii", "mark:export"]
    assert _principal_marking_tokens([]) == ["mark:__none__"]


def test_marking_filter_allows_unmarked_or_fully_held() -> None:
    clause = _marking_filter(["mark:pii"])
    should = clause["bool"]["should"]
    assert any("must_not" in (item.get("bool") or {}) for item in should)
    terms_set = next(item["terms_set"] for item in should if "terms_set" in item)
    assert terms_set["required_markings"]["minimum_should_match_field"] == "required_marking_count"
    assert terms_set["required_markings"]["terms"] == ["mark:pii"]

def test_denied_query_does_not_search_confidential_fields() -> None:
    query = build_search_query(
        principal=_principal("JP"),
        query_text='"contact@acme-robotics.example"',
        include_confidential=False,
    )
    assert query["query"]["bool"]["must"][0]["simple_query_string"]["fields"] == ["text"]
    assert query["query"]["bool"]["must"][0]["simple_query_string"]["query"] == '"contact@acme-robotics.example"'

