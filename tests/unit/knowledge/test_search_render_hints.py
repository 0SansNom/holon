"""Unit tests for searchable/sortable property render hints in search indexing."""

from __future__ import annotations

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_DIR = REPO_ROOT / "services" / "knowledge"
LIBS = REPO_ROOT / "libs"

sys.path.insert(0, str(LIBS))
sys.path.insert(0, str(KNOWLEDGE_DIR))

# `app.search` imports httpx at module load; stub it for pure helper tests.
sys.modules.setdefault("httpx", types.ModuleType("httpx"))

from app.search import (  # noqa: E402
    _searchable_columns,
    _sortable_prop_values,
    selectable_property_names,
)


def test_searchable_columns_default_includes_all() -> None:
    mapping = {"id": "id", "name": "name", "secret": "ssn"}
    assert _searchable_columns(mapping, None) == ["id", "name", "ssn"]
    assert _searchable_columns(mapping, {}) == ["id", "name", "ssn"]


def test_searchable_columns_omits_when_hints_exclude_searchable() -> None:
    mapping = {"id": "id", "name": "name", "secret": "ssn"}
    types = {
        "secret": {"render_hints": ["sortable"]},
        "name": {"render_hints": ["searchable", "sortable"]},
    }
    assert _searchable_columns(mapping, types) == ["id", "name"]


def test_sortable_prop_values_only_for_sortable_hint() -> None:
    mapping = {"id": "id", "name": "name", "score": "score"}
    types = {
        "name": {"render_hints": ["searchable", "sortable"]},
        "score": {"render_hints": ["sortable"]},
    }
    row = {"id": "1", "name": "Ada", "score": 42}
    assert _sortable_prop_values(row, mapping, types) == {"name": "Ada", "score": "42"}
    assert _sortable_prop_values(row, mapping, None) == {}


def test_keyword_prop_values_includes_selectable_and_low_cardinality() -> None:
    mapping = {"status": "status", "tier": "tier", "name": "name"}
    types = {
        "status": {"render_hints": ["searchable", "selectable"]},
        "tier": {"render_hints": ["searchable", "low_cardinality"]},
        "name": {"render_hints": ["searchable"]},
    }
    row = {"status": "open", "tier": "gold", "name": "Ada"}
    assert _sortable_prop_values(row, mapping, types) == {"status": "open", "tier": "gold"}
    assert selectable_property_names(types) == ["status", "tier"]


def test_keyword_prop_values_uses_field_column_overlay() -> None:
    mapping = {"address": "address_json"}
    types = {
        "address": {
            "kind": "struct",
            "properties": {
                "city": {"kind": "value_type", "value_type": "String", "column": "city_col"},
            },
        }
    }
    row = {"address_json": '{"city": "Old"}', "city_col": "Paris"}
    assert _sortable_prop_values(row, mapping, types) == {"address": {"city": "Paris"}}


def test_struct_field_text_fragments_for_searchable_struct() -> None:
    from app.search import _struct_field_text_fragments

    mapping = {"address": "address_json", "secret": "secret_json"}
    types = {
        "address": {
            "kind": "struct",
            "render_hints": ["searchable"],
            "properties": {"city": {"kind": "value_type", "value_type": "String"}},
        },
        "secret": {
            "kind": "struct",
            "render_hints": ["sortable"],
            "properties": {"token": {"kind": "value_type", "value_type": "String"}},
        },
    }
    row = {
        "address_json": {"city": "Paris"},
        "secret_json": {"token": "nope"},
    }
    assert _struct_field_text_fragments(row, mapping, types) == ["Paris"]


def test_selectable_property_names_fans_out_struct_fields() -> None:
    types = {
        "address": {
            "kind": "struct",
            "render_hints": ["searchable", "selectable"],
            "properties": {
                "city": {"kind": "value_type", "value_type": "String"},
                "zip": {"kind": "value_type", "value_type": "String"},
            },
        }
    }
    assert selectable_property_names(types) == ["address.city", "address.zip"]


def test_build_post_filter_struct_field_path() -> None:
    from app.search import _build_post_filter

    pf = _build_post_filter(property_filters={"address.city": "Paris"})
    assert pf == {"term": {"props.address.city": "Paris"}}


def test_build_post_filter_combines_object_type_and_props() -> None:
    from app.search import _build_post_filter, _text_query_clause

    pf = _build_post_filter(object_type="Customer", property_filters={"status": "open"})
    assert pf == {
        "bool": {
            "filter": [
                {"term": {"object_type": "Customer"}},
                {"term": {"props.status": "open"}},
            ]
        }
    }
    assert _text_query_clause("*Acme", allow_leading_wildcards=True)["query_string"]["allow_leading_wildcard"] is True
    assert "regexp" in _text_query_clause("/Acme.*/", allow_regex=True)


def test_build_post_filter_object_types_terms() -> None:
    from app.search import _build_post_filter

    pf = _build_post_filter(object_types=["Supplier", "Customer"])
    assert pf == {"terms": {"object_type": ["Supplier", "Customer"]}}

    narrowed = _build_post_filter(object_type="Supplier", object_types=["Supplier", "Customer"])
    assert narrowed == {"term": {"object_type": "Supplier"}}


def test_searchable_columns_inherits_spt_render_hints() -> None:
    mapping = {"startDate": "start_date", "name": "name"}
    types = {"startDate": {"kind": "shared_property_type", "shared_property_type": "StartDate"}}
    shared = {"StartDate": {"api_name": "StartDate", "render_hints": ["sortable"], "aliases": ["hire date"]}}
    # SPT hints omit searchable → column excluded
    assert _searchable_columns(mapping, types, shared) == ["name"]


def test_ontology_alias_terms_includes_spt_aliases() -> None:
    from app.search import _ontology_alias_terms

    mapping = {"startDate": "start_date"}
    types = {"startDate": {"kind": "shared_property_type", "shared_property_type": "StartDate"}}
    shared = {
        "StartDate": {
            "api_name": "StartDate",
            "display_name": "Start date",
            "aliases": ["Hire Date", "hire date", "Onboarding"],
        }
    }
    assert _ontology_alias_terms(mapping, types, shared) == [
        "startDate",
        "Start date",
        "Hire Date",
        "Onboarding",
    ]
