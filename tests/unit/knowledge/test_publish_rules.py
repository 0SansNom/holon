"""Publish-time rules that do not need Postgres, Iceberg, or the stack.

Covers the pure validators and the async ones behind a fake registry:
formats, property types, derived properties, value types, relation storage,
lifecycle, render hints, type classes, and URN builders.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime

import pytest

from app.ontology import (
    lifecycle,
    publishing_validate,
    publishing_validate_derived as derived,
    publishing_validate_formats as formats,
    publishing_validate_types as types,
    relation_type_validate as relations,
    render_hints,
    type_classes,
    urns,
    value_type_validate as values,
)


def _run(coro):
    return asyncio.run(coro)


def _raises(fn, match: str):
    with pytest.raises(ValueError, match=match):
        fn()


# --- urns ---


def test_urn_builders():
    assert urns.object_type_urn("acme", "main", "Customer") == "hl:acme:main:object-type:Customer"
    assert urns.relation_type_urn("acme", "main", "placed") == "hl:acme:main:relation-type:placed"
    assert urns.workspace_urn("acme", "main") == "hl:acme:global:workspace:main"


# --- type classes ---


def test_type_class_normalization_and_lookup():
    assert type_classes.normalize_type_class("  priority ") == "priority"
    assert type_classes.normalize_type_classes(None) == []
    assert type_classes.normalize_type_classes(["display:media_url"]) == ["display:media_url"]
    assert type_classes.parse_type_class("display:media_url") == ("display", "media_url")
    assert type_classes.parse_type_class("priority") == ("custom", "priority")
    _raises(lambda: type_classes.normalize_type_class("  "), "non-empty")
    _raises(lambda: type_classes.normalize_type_class("Bad"), "invalid type class")
    _raises(lambda: type_classes.normalize_type_classes("nope"), "list of strings")
    _raises(lambda: type_classes.normalize_type_classes([1]), "list of strings")

    classes = ["display:icon", "not a class", 3]
    assert type_classes.has_type_class(classes, "display", "icon") is True
    assert type_classes.has_type_class(["not a class", 3], "display", "icon") is False
    assert type_classes.has_type_class(None, "display", "icon") is False
    assert type_classes.find_property_with_type_class(None, "display", "icon") is None
    props = {
        "skip": "nope",
        "icon": {"type_classes": ["display:icon"]},
    }
    assert type_classes.find_property_with_type_class(props, "display", "icon") == "icon"
    assert type_classes.find_property_with_type_class(props, "display", "missing") is None


# --- render hints ---


def test_render_hints_validation_and_facets():
    assert render_hints.normalize_render_hints(None) == []
    assert render_hints.normalize_render_hints(None, default=["searchable"]) == ["searchable"]
    assert render_hints.normalize_render_hints([" searchable ", "", "searchable", "sortable"]) == [
        "searchable",
        "sortable",
    ]
    _raises(lambda: render_hints.normalize_render_hints("searchable"), "list of strings")
    _raises(lambda: render_hints.normalize_render_hints(["nope"]), "unknown render_hints")
    _raises(lambda: render_hints.normalize_render_hints(["sortable"]), "require 'searchable'")
    assert render_hints.has_render_hint(["searchable"], "searchable") is True
    assert render_hints.has_render_hint(None, "searchable") is False

    props = {
        "skip": "x",
        "plain": {"render_hints": ["identifier"]},
        "city": {"render_hints": ["selectable", "searchable"]},
        "address": {
            "kind": "struct",
            "render_hints": ["low_cardinality", "searchable"],
            "properties": {"city": {}, "zip": {}},
        },
        "empty_struct": {"kind": "struct", "render_hints": ["selectable"], "properties": {}},
    }
    assert render_hints.facet_render_hints(None) == []
    assert render_hints.facet_render_hints(props) == ["city", "address.city", "address.zip", "empty_struct"]
    assert render_hints.search_capability_hints({"skip": "x"}) == {
        "allow_leading_wildcards": False,
        "allow_regex_queries": False,
    }
    assert render_hints.search_capability_hints(None) == {
        "allow_leading_wildcards": False,
        "allow_regex_queries": False,
    }
    assert render_hints.search_capability_hints(
        {"q": {"render_hints": ["enable_leading_wildcards", "enable_regex_queries", "searchable"]}}
    ) == {"allow_leading_wildcards": True, "allow_regex_queries": True}


# --- lifecycle ---


def test_lifecycle_status_and_deprecation():
    assert lifecycle.normalize_lifecycle_status("active") == "active"
    _raises(lambda: lifecycle.normalize_lifecycle_status("nope"), "invalid lifecycle_status")
    _raises(lambda: lifecycle.assert_lifecycle_for_target("promoted", target="property"), "only valid on ObjectType")
    _raises(lambda: lifecycle.assert_lifecycle_for_target("promoted", target="registry"), "only valid on ObjectType")
    assert lifecycle.assert_lifecycle_for_target("promoted", target="object_type") == "promoted"
    assert lifecycle.assert_lifecycle_for_target("active", target="property") == "active"
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(lifecycle, "PROPERTY_LIFECYCLE_STATUSES", frozenset({"active"}))
    monkeypatch.setattr(lifecycle, "REGISTRY_LIFECYCLE_STATUSES", frozenset({"active"}))
    try:
        _raises(lambda: lifecycle.assert_lifecycle_for_target("experimental", target="property"), "property lifecycle")
        _raises(lambda: lifecycle.assert_lifecycle_for_target("experimental", target="registry"), "lifecycle_status must be")
    finally:
        monkeypatch.undo()

    cleared = lifecycle.normalize_deprecation_metadata("active", deprecation_reason="stale", target="property")
    assert cleared["deprecation_reason"] is None
    assert cleared["deprecation_deadline"] is None

    deadline = date(2026, 1, 2)
    meta = lifecycle.normalize_deprecation_metadata(
        "deprecated",
        deprecation_reason="  replaced  ",
        deprecation_deadline=datetime(2026, 1, 2, 3, 4),
        replacement_urn="  hl:acme:main:object-type:Next  ",
        target="registry",
    )
    assert meta["deprecation_reason"] == "replaced"
    assert meta["deprecation_deadline"] == deadline
    assert meta["replacement_urn"].endswith("Next")
    assert lifecycle.normalize_deprecation_metadata(
        "deprecated",
        deprecation_reason="gone",
        deprecation_deadline=deadline,
        replacement_urn="   ",
    )["replacement_urn"] is None
    assert lifecycle.normalize_deprecation_metadata(
        "deprecated", deprecation_reason="gone", deprecation_deadline="2026-01-02"
    )["deprecation_deadline"] == deadline
    assert lifecycle._parse_deadline(None) is None
    assert lifecycle._parse_deadline("   ") is None

    _raises(
        lambda: lifecycle.normalize_deprecation_metadata("deprecated", deprecation_reason="  "),
        "deprecation_reason is required",
    )
    _raises(
        lambda: lifecycle.normalize_deprecation_metadata("deprecated", deprecation_reason="x" * 2001, deprecation_deadline=deadline),
        "too long",
    )
    _raises(
        lambda: lifecycle.normalize_deprecation_metadata("deprecated", deprecation_reason="gone"),
        "deprecation_deadline is required",
    )
    _raises(
        lambda: lifecycle.normalize_deprecation_metadata("deprecated", deprecation_reason="gone", deprecation_deadline="not-a-date"),
        "ISO date",
    )
    _raises(
        lambda: lifecycle.normalize_deprecation_metadata("deprecated", deprecation_reason="gone", deprecation_deadline=1),
        "ISO date",
    )
    _raises(
        lambda: lifecycle.normalize_deprecation_metadata(
            "deprecated", deprecation_reason="gone", deprecation_deadline=deadline, replacement_urn="u" * 513
        ),
        "replacement_urn is too long",
    )


# --- value types ---


def _vt(base: str, **extra) -> dict:
    row = {"name": "Field", "base_type": base, "constraints": []}
    row.update(extra)
    return row


def test_value_type_constraints_and_checks():
    values._validate_constraints("string", [{"kind": "enum", "values": ["a"]}, {"kind": "uuid"}, {"kind": "rid"}])
    values._validate_constraints("integer", [{"kind": "range", "min": 1}])
    _raises(lambda: values._validate_constraints("string", ["nope"]), "must be an object")
    _raises(lambda: values._validate_constraints("string", [{"kind": "nope"}]), "unknown kind")
    _raises(lambda: values._validate_constraints("string", [{"kind": "enum", "values": []}]), "non-empty")
    _raises(lambda: values._validate_constraints("boolean", [{"kind": "range", "min": 1}]), "isn't meaningful")
    _raises(lambda: values._validate_constraints("integer", [{"kind": "range"}]), "requires 'min'")
    _raises(lambda: values._validate_constraints("integer", [{"kind": "uuid"}]), "only applies")

    assert values.validate_value("aa", _vt("string", format_regex=r"a+", format_regex_match="full")) is None
    assert "substring" in values.validate_value("zz", _vt("string", format_regex=r"a+", format_regex_match="substring"))
    assert "full match" in values.validate_value("ba", _vt("string", format_regex=r"a+"))
    assert values.validate_value("a", _vt("string", constraints=[{"kind": "enum", "values": ["A"], "caseSensitive": False}])) is None
    assert values.validate_value(1, _vt("string", constraints=[{"kind": "enum", "values": ["A"], "caseSensitive": False}]))
    assert values._check_constraint("b", {"kind": "enum", "values": ["a"]}, "string", "Field")
    assert values._check_constraint(1, {"kind": "range", "min": 2, "max": 4}, "integer", "Field")
    assert values._check_constraint(5, {"kind": "range", "max": 4}, "integer", "Field")
    assert values._check_constraint("abcd", {"kind": "range", "min": 1, "max": 3}, "string", "Field")
    assert values._check_constraint("ab", {"kind": "range", "min": 1, "max": 3}, "string", "Field") is None
    assert values.validate_value("hl:acme:main:object-type:Customer", _vt("string", constraints=[{"kind": "rid"}])) is None
    assert "resource identifier" in values.validate_value("nope", _vt("string", constraints=[{"kind": "rid"}]))
    assert values.validate_value("00112233-4455-6677-8899-aabbccddeeff", _vt("string", constraints=[{"kind": "uuid"}])) is None
    assert "UUID" in values.validate_value("nope", _vt("string", constraints=[{"kind": "uuid"}]))
    assert values._check_constraint(1, {"kind": "other"}, "integer", "Field") is None

    assert values.validate_value(3, _vt("integer")) is None
    assert values.validate_value(True, _vt("integer"))
    assert values.validate_value(1.2, _vt("integer"))
    assert values.validate_value(1, _vt("double")) is None
    assert values.validate_value(True, _vt("double"))
    assert values.validate_value("x", _vt("double"))
    assert values.validate_value(True, _vt("boolean")) is None
    assert values.validate_value(1, _vt("boolean"))
    assert values.validate_value("2026-01-02", _vt("date")) is None
    assert values.validate_value("nope", _vt("date"))
    assert values.validate_value(1, _vt("timestamp"))
    assert values.validate_value("2026-01-02T00:00:00", _vt("timestamp")) is None
    assert values.validate_value("1.5,2", _vt("geopoint")) is None
    assert values.validate_value("north", _vt("geopoint"))
    shape = json.dumps({"type": "Point", "coordinates": [1, 2]})
    assert values.validate_value(shape, _vt("geoshape")) is None
    assert values.validate_value("{", _vt("geoshape"))
    assert values.validate_value(json.dumps({"type": "Nope"}), _vt("geoshape"))
    assert values.validate_value(1, _vt("geoshape"))
    assert values.validate_value([1, 2.0], _vt("vector")) is None
    assert values.validate_value([True], _vt("vector"))
    assert values.validate_value([], _vt("vector"))
    assert "unknown base_type" in values._check_base_type(1, "matrix", "Field")


# --- relation storage ---


def test_relation_storage_and_side_metadata():
    assert relations._parse_jsonb(None, default=[]) == []
    assert relations._parse_jsonb(["a"], default=[]) == ["a"]
    assert relations._parse_jsonb({"a": 1}, default={}) == {"a": 1}
    assert relations._parse_jsonb("nope", default=[]) == []
    assert relations._parse_jsonb(1, default=[]) == []
    assert relations._parse_jsonb('["a"]', default=[]) == ["a"]
    row = relations._row_to_dict({"type_classes": '["priority"]', "name": "placed"})
    assert row["type_classes"] == ["priority"]
    assert relations._local_accessor("Order.customer") == "customer"
    assert relations._local_accessor("customer") == "customer"
    assert relations._normalize_side_metadata(
        display_name="", plural_display_name="", api_name=" orders ", visibility="normal", side="source"
    )[2] == "orders"
    _raises(
        lambda: relations._normalize_side_metadata(
            display_name="", plural_display_name="", api_name="a", visibility="nope", side="source"
        ),
        "source_visibility",
    )
    _raises(
        lambda: relations._normalize_side_metadata(
            display_name="", plural_display_name="", api_name="  ", visibility="hidden", side="target"
        ),
        "target_api_name",
    )
    assert relations._normalize_type_classes(None) == []
    assert relations._normalize_type_classes(["priority"]) == ["priority"]

    relations._validate_storage(
        storage_kind="foreign_key", cardinality="many_to_one", source_property="customer_id",
        join_dataset_urn=None, join_source_column=None, join_target_column=None,
        mid_object_type_urn=None, mid_source_property=None, mid_target_property=None,
    )
    relations._validate_storage(
        storage_kind="join_dataset", cardinality="many_to_many", source_property="",
        join_dataset_urn="hl:acme:main:dataset:bridge", join_source_column="a", join_target_column="b",
        mid_object_type_urn=None, mid_source_property=None, mid_target_property=None,
    )
    relations._validate_storage(
        storage_kind="object_backed", cardinality="many_to_many", source_property="",
        join_dataset_urn=None, join_source_column=None, join_target_column=None,
        mid_object_type_urn="hl:acme:main:object-type:Membership",
        mid_source_property="left_id", mid_target_property="right_id",
    )
    _raises(lambda: relations._validate_storage(
        storage_kind="nope", cardinality="many_to_one", source_property="id",
        join_dataset_urn=None, join_source_column=None, join_target_column=None,
        mid_object_type_urn=None, mid_source_property=None, mid_target_property=None,
    ), "invalid storage_kind")
    _raises(lambda: relations._validate_storage(
        storage_kind="foreign_key", cardinality="many_to_one", source_property="",
        join_dataset_urn=None, join_source_column=None, join_target_column=None,
        mid_object_type_urn=None, mid_source_property=None, mid_target_property=None,
    ), "source_property is required")
    _raises(lambda: relations._validate_storage(
        storage_kind="join_dataset", cardinality="one_to_many", source_property="",
        join_dataset_urn="u", join_source_column="a", join_target_column="b",
        mid_object_type_urn=None, mid_source_property=None, mid_target_property=None,
    ), "many_to_many")
    _raises(lambda: relations._validate_storage(
        storage_kind="join_dataset", cardinality="many_to_many", source_property="",
        join_dataset_urn=None, join_source_column="a", join_target_column="b",
        mid_object_type_urn=None, mid_source_property=None, mid_target_property=None,
    ), "join_dataset requires")
    _raises(lambda: relations._validate_storage(
        storage_kind="object_backed", cardinality="many_to_many", source_property="",
        join_dataset_urn=None, join_source_column=None, join_target_column=None,
        mid_object_type_urn=None, mid_source_property="a", mid_target_property="b",
    ), "object_backed requires")


# --- formats ---


def _formats(**rules):
    formats._validate_property_formats(
        property_mapping={"name": "name", "amount": "amount", "when": "when", "owner": "owner", "link": "link"},
        derived_properties={"score": "fn"},
        property_formats=rules,
    )


def test_property_and_conditional_formats():
    _formats(amount={"kind": "currency", "currency": "USD"})
    _formats(name={"kind": "badge", "colors": {"ok": "success"}})
    _formats(amount={
        "kind": "numeric", "style": "unit", "unit": "kilogram", "notation": "compact",
        "minimumFractionDigits": 1, "prefix": "$", "suffix": "kg", "useGrouping": True,
    })
    _formats(when={"kind": "datetime", "style": "iso8601", "timezone": "UTC"})
    _formats(owner={"kind": "principal"})
    _formats(link={"kind": "resource-link", "resourceType": "object-type"})
    _formats(score={"kind": "numeric"})

    _raises(lambda: _formats(missing={"kind": "currency", "currency": "USD"}), "doesn't have")
    _raises(lambda: _formats(name={"kind": "nope"}), "unknown kind")
    _raises(lambda: _formats(name={"kind": "currency", "currency": "US"}), "3-letter")
    _raises(lambda: _formats(name={"kind": "badge", "colors": {}}), "non-empty")
    _raises(lambda: _formats(name={"kind": "badge", "colors": {"a": "neon"}}), "unknown badge")
    _raises(lambda: _formats(amount={"kind": "numeric", "style": "nope"}), "unknown numeric")
    _raises(lambda: _formats(amount={"kind": "numeric", "style": "currency", "currency": "U"}), "3-letter")
    _raises(lambda: _formats(amount={"kind": "numeric", "style": "unit"}), "requires a 'unit'")
    _raises(lambda: _formats(amount={"kind": "numeric", "notation": "nope"}), "unknown notation")
    _raises(lambda: _formats(amount={"kind": "numeric", "minimumFractionDigits": "1"}), "must be an integer")
    _raises(lambda: _formats(amount={"kind": "numeric", "prefix": 1}), "must be a string")
    _raises(lambda: _formats(amount={"kind": "numeric", "useGrouping": "yes"}), "boolean")
    _raises(lambda: _formats(when={"kind": "datetime", "style": "nope"}), "unknown datetime")
    _raises(lambda: _formats(when={"kind": "datetime", "style": "date", "timezone": 1}), "IANA")
    _raises(lambda: _formats(link={"kind": "resource-link", "resourceType": "nope"}), "resourceType")

    formats._validate_conditional_formats(
        property_mapping={"amount": "amount"},
        derived_properties={},
        conditional_formats={"amount": [{
            "condition": {"type": "always"},
            "style": {"color": "#abc", "backgroundColor": "success", "textAlign": "right"},
        }]},
    )
    for condition in (
        {"type": "string-equals", "value": "a"},
        {"type": "string-contains", "value": "a"},
        {"type": "string-starts-with", "value": "a"},
        {"type": "number-equals", "value": 1},
        {"type": "number-range", "min": 1},
        {"type": "is-null"},
    ):
        formats._validate_conditional_formats(
            property_mapping={"amount": "amount", "name": "name"},
            derived_properties={},
            conditional_formats={"amount": [{
                "condition": condition,
                "compareTo": {"kind": "property", "property": "name"},
                "style": {"color": "primary"},
            }]},
        )
    _raises(lambda: formats._validate_conditional_formats(
        property_mapping={}, derived_properties={}, conditional_formats={"nope": [{"condition": {"type": "always"}, "style": {"color": "primary"}}]}
    ), "doesn't have")
    _raises(lambda: formats._validate_conditional_formats(
        property_mapping={"amount": "amount"}, derived_properties={}, conditional_formats={"amount": []}
    ), "non-empty list")
    _raises(lambda: formats._validate_conditional_formats(
        property_mapping={"amount": "amount"}, derived_properties={}, conditional_formats={"amount": ["x"]}
    ), "must be an object")
    _raises(lambda: formats._validate_conditional_formats(
        property_mapping={"amount": "amount"}, derived_properties={}, conditional_formats={"amount": [{"condition": {}, "style": {"color": "primary"}}]}
    ), "condition.type")
    _raises(lambda: formats._validate_conditional_formats(
        property_mapping={"amount": "amount"}, derived_properties={},
        conditional_formats={"amount": [{"condition": {"type": "string-equals"}, "style": {"color": "primary"}}]},
    ), "requires a string")
    _raises(lambda: formats._validate_conditional_formats(
        property_mapping={"amount": "amount"}, derived_properties={},
        conditional_formats={"amount": [{"condition": {"type": "number-equals", "value": "1"}, "style": {"color": "primary"}}]},
    ), "numeric")
    _raises(lambda: formats._validate_conditional_formats(
        property_mapping={"amount": "amount"}, derived_properties={},
        conditional_formats={"amount": [{"condition": {"type": "number-range"}, "style": {"color": "primary"}}]},
    ), "min")
    _raises(lambda: formats._validate_conditional_formats(
        property_mapping={"amount": "amount"}, derived_properties={},
        conditional_formats={"amount": [{"condition": {"type": "always"}, "compareTo": {"kind": "nope"}, "style": {"color": "primary"}}]},
    ), "compareTo")
    _raises(lambda: formats._validate_style("amount", 0, {}), "non-empty")
    _raises(lambda: formats._validate_style("amount", 0, {"color": "neon"}), "hex color")
    _raises(lambda: formats._validate_style("amount", 0, {"backgroundColor": "neon"}), "backgroundColor")
    _raises(lambda: formats._validate_style("amount", 0, {"textAlign": "justify"}), "textAlign")


def test_struct_field_and_property_control_metadata():
    formats._validate_struct_field_metadata("address.city", {"description": "c", "main_field": True, "column": " city "})
    assert formats._validate_struct_field_metadata("address.city", {}) is None
    _raises(lambda: formats._validate_struct_field_metadata("p", {"description": 1}), "description")
    _raises(lambda: formats._validate_struct_field_metadata("p", {"main_field": "yes"}), "main_field")
    _raises(lambda: formats._validate_struct_field_metadata("p", {"column": "c"}, allow_column=False), "only allowed")
    _raises(lambda: formats._validate_struct_field_metadata("p", {"column": "  "}), "non-empty")

    formats._validate_property_control_metadata("name", {"description": "d"}, nested=True)
    formats._validate_property_control_metadata(
        "name",
        {"editable": True, "required": False, "visibility": "prominent", "lifecycle_status": "active",
         "render_hints": ["searchable"], "type_classes": ["priority"]},
        nested=False,
    )
    _raises(lambda: formats._validate_property_control_metadata("name", {"editable": True}, nested=True), "only applies")
    _raises(lambda: formats._validate_property_control_metadata("name", {"editable": "yes"}, nested=False), "boolean")
    _raises(lambda: formats._validate_property_control_metadata("name", {"visibility": "nope"}, nested=False), "visibility")
    _raises(lambda: formats._validate_property_control_metadata("name", {"lifecycle_status": "promoted"}, nested=False), "lifecycle_status")
    _raises(lambda: formats._validate_property_control_metadata("name", {"render_hints": ["nope"]}, nested=False), "render_hints")
    _raises(lambda: formats._validate_property_control_metadata("name", {"type_classes": ["Bad"]}, nested=False), "invalid type class")


# --- property types (fake registries) ---


def _patch_types(monkeypatch, *, value_types=None, shared=None):
    value_types = value_types if value_types is not None else {"Email"}
    shared = shared if shared is not None else {"Scalar": {"struct_properties": None}, "Struct": {"struct_properties": {"city": {}}}}

    async def get_value_type(_pool, _tenant, name):
        return {"name": name} if name in value_types else None

    async def get_shared(_pool, _tenant, name):
        return shared.get(name)

    monkeypatch.setattr("app.ontology.value_types.get_value_type", get_value_type)
    monkeypatch.setattr("app.ontology.shared_property_types.get_shared_property_type", get_shared)


def _types(monkeypatch, property_types, *, mapping=None, derived=None):
    _patch_types(monkeypatch)
    return _run(types._validate_property_types(
        None,
        tenant_id="acme",
        property_mapping=mapping if mapping is not None else {"name": "name", "tags": "tags", "address": "address"},
        derived_properties=derived or {},
        property_types=property_types,
    ))


def test_property_type_shapes(monkeypatch):
    _types(monkeypatch, {"name": {"visibility": "hidden"}})
    _types(monkeypatch, {"name": {"kind": "value_type", "value_type": "Email"}})
    _types(monkeypatch, {"name": {"kind": "shared_property_type", "shared_property_type": "Scalar"}})
    _types(monkeypatch, {"address": {
        "kind": "struct",
        "properties": {"city": {"kind": "value_type", "value_type": "Email", "column": "city"}},
    }})
    _types(monkeypatch, {"tags": {
        "kind": "array",
        "unique_elements": True,
        "element": {"kind": "struct", "properties": {"label": {"kind": "value_type", "value_type": "Email"}}},
    }})
    _raises(lambda: _types(monkeypatch, {"missing": {"kind": "value_type", "value_type": "Email"}}), "doesn't have")
    _raises(lambda: _types(monkeypatch, {"address": {"kind": "struct", "properties": {"city": {}}}}), "require a kind")
    _raises(lambda: _types(monkeypatch, {"name": {"kind": "nope"}}), "unknown kind")
    _raises(lambda: _types(monkeypatch, {"name": {"kind": "value_type", "value_type": "Missing"}}), "unknown value_type")
    _raises(lambda: _types(monkeypatch, {"name": {"kind": "shared_property_type", "shared_property_type": "Missing"}}), "unknown shared")
    _raises(lambda: _types(monkeypatch, {"address": {
        "kind": "struct",
        "properties": {"city": {"kind": "shared_property_type", "shared_property_type": "Struct"}},
    }}), "can't be nested")
    _raises(lambda: _types(monkeypatch, {"address": {"kind": "struct", "properties": {}}}), "non-empty")
    _raises(lambda: _types(monkeypatch, {"address": {
        "kind": "struct",
        "properties": {"city": {"kind": "struct", "properties": {"zip": {"kind": "value_type", "value_type": "Email"}}}},
    }}), "one level")
    _raises(lambda: _types(monkeypatch, {"tags": {"kind": "array", "element": {"kind": "array", "element": {"kind": "value_type", "value_type": "Email"}}}}), "one level")
    _raises(lambda: _types(monkeypatch, {"tags": {"kind": "array"}}), "requires an 'element'")
    _raises(lambda: _types(monkeypatch, {"tags": {"kind": "array", "unique_elements": "yes", "element": {"kind": "value_type", "value_type": "Email"}}}), "unique_elements")
    _raises(lambda: _types(monkeypatch, {"tags": {
        "kind": "array",
        "element": {"kind": "struct", "properties": {"label": {"kind": "value_type", "value_type": "Email", "column": "label"}}},
    }}), "only allowed")


class _Resp:
    def __init__(self, code: int):
        self.status_code = code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _Client:
    def __init__(self, code: int):
        self.code = code
        self.url = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, headers=None):
        self.url = url
        return _Resp(self.code)


def test_project_scope_asks_identity(monkeypatch):
    client = _Client(200)
    monkeypatch.setattr(types.httpx, "AsyncClient", lambda **_kwargs: client)
    _run(types._validate_project_scope(
        identity_url="http://identity", project_urn="hl:acme:global:project:ops", identity_token="t"
    ))
    assert client.url.endswith("/projects/ops")

    monkeypatch.setattr(types.httpx, "AsyncClient", lambda **_kwargs: _Client(404))
    _raises(lambda: _run(types._validate_project_scope(
        identity_url="http://identity", project_urn="hl:acme:global:project:missing", identity_token="t"
    )), "unknown project")

    monkeypatch.setattr(types.httpx, "AsyncClient", lambda **_kwargs: _Client(500))
    with pytest.raises(RuntimeError, match="http 500"):
        _run(types._validate_project_scope(
            identity_url="http://identity", project_urn="hl:acme:global:project:ops", identity_token="t"
        ))


# --- derived properties ---


def _rel(source: str, target: str, api: str, *, reverse: str = "", target_property: str | None = None) -> dict:
    return {
        "name": f"{source}.{api}",
        "source_object_type_urn": f"hl:acme:main:object-type:{source}",
        "target_object_type_urn": f"hl:acme:main:object-type:{target}",
        "source_api_name": api,
        "target_api_name": reverse,
        "target_property": target_property,
    }


def _patch_derived(monkeypatch, *, functions=None, rels=None, objects=None):
    functions = functions or {}
    rels = rels or []
    objects = objects or {}

    async def find_active(_pool, name):
        return functions.get(name)

    async def list_relation_types(_pool, _tenant):
        return rels

    async def get_object_type(_pool, urn):
        return objects.get(urn)

    monkeypatch.setattr("app.function_registry.find_active_function_by_name", find_active)
    monkeypatch.setattr("app.ontology.relation_types.list_relation_types", list_relation_types)
    monkeypatch.setattr("app.ontology.object_types.get_object_type", get_object_type)
    monkeypatch.setattr(derived, "get_object_type", get_object_type)


def _derived(monkeypatch, specs, **kwargs):
    property_types = kwargs.pop("property_types", None)
    _patch_derived(monkeypatch, **kwargs)
    return _run(derived._validate_derived_properties(
        None, derived_properties=specs, object_type_name="Customer", tenant_id="acme",
        property_types=property_types,
    ))


def test_derived_property_rules(monkeypatch):
    assert derived._link_aggregate_path({}) == []
    assert derived._link_aggregate_path({"path": ["orders"]}) == ["orders"]
    order = "hl:acme:main:object-type:Order"
    placed = _rel("Customer", "Order", "orders", reverse="customer", target_property="customer")
    assert derived._find_relation_by_link_name([placed], "Customer", "orders") is placed
    assert derived._find_relation_by_link_name([placed], "Order", "customer") is placed
    bare = dict(placed, source_api_name="", target_api_name="")
    assert derived._find_relation_by_link_name([bare], "Customer", "orders") is bare
    assert derived._find_relation_by_link_name([bare], "Order", "customer") is bare
    assert derived._find_relation_by_link_name([placed], "Customer", "missing") is None
    fallback = dict(placed, source_api_name="purchases", target_api_name="buyer", target_property="buyer_id")
    assert derived._find_relation_by_link_name([fallback], "Customer", "orders") is fallback
    assert derived._find_relation_by_link_name([fallback], "Order", "buyer_id") is fallback

    _derived(monkeypatch, {"score": "scoreFn"}, functions={"scoreFn": {"name": "scoreFn"}})
    _raises(lambda: _derived(monkeypatch, {"score": "missing"}), "not a registered")
    _raises(lambda: _derived(monkeypatch, {"score": {"kind": "nope"}}), "must be either")
    _raises(lambda: _derived(monkeypatch, {"score": 1}), "must be either")

    array = {"tags": {"kind": "array", "element": {"kind": "struct", "properties": {"rank": {}}}}}
    _derived(
        monkeypatch,
        {"top": {"kind": "struct_reducer", "property": "tags", "reducer": "max", "by": "rank"}},
        property_types=array,
    )
    scalar = {"tags": {"kind": "array", "element": {"kind": "value_type"}}}
    _derived(
        monkeypatch,
        {"top": {"kind": "struct_reducer", "property": "tags", "reducer": "first"}},
        property_types=scalar,
    )
    _raises(lambda: _derived(monkeypatch, {"top": {"kind": "struct_reducer", "property": "missing", "reducer": "first"}}), "isn't an 'array'")
    _raises(lambda: _derived(
        monkeypatch, {"top": {"kind": "struct_reducer", "property": "tags", "reducer": "nope"}}, property_types=scalar
    ), "unknown reducer")
    _raises(lambda: _derived(
        monkeypatch, {"top": {"kind": "struct_reducer", "property": "tags", "reducer": "max"}}, property_types=array
    ), "requires")
    _raises(lambda: _derived(
        monkeypatch, {"top": {"kind": "struct_reducer", "property": "tags", "reducer": "max", "by": "rank"}}, property_types=scalar
    ), "must not set")

    rels = [placed, _rel("Order", "Item", "items")]
    objects = {order: {"property_mapping": {"amount": "amount"}}}
    _derived(monkeypatch, {"n": {"kind": "link_aggregate", "aggregate": "count", "path": ["orders"]}}, rels=rels)
    _derived(
        monkeypatch,
        {"total": {"kind": "link_aggregate", "aggregate": "sum", "path": ["orders"], "property": "amount", "collect_limit": 2}},
        rels=rels, objects=objects,
    )
    _raises(lambda: _derived(monkeypatch, {"n": {"kind": "link_aggregate", "aggregate": "nope", "path": ["orders"]}}, rels=rels), "unknown aggregate")
    _raises(lambda: _derived(monkeypatch, {"n": {"kind": "link_aggregate", "aggregate": "count", "path": ["orders"], "collect_limit": True}}, rels=rels), "positive integer")
    _raises(lambda: _derived(monkeypatch, {"n": {"kind": "link_aggregate", "aggregate": "count", "path": ["orders"], "collect_limit": 0}}, rels=rels), "positive integer")
    _raises(lambda: _derived(monkeypatch, {"n": {"kind": "link_aggregate", "aggregate": "count"}}, rels=rels), "requires 'path'")
    _raises(lambda: _derived(monkeypatch, {"n": {"kind": "link_aggregate", "aggregate": "count", "path": ["a", "b", "c", "d"]}}, rels=rels), "requires 'path'")
    _raises(lambda: _derived(monkeypatch, {"n": {"kind": "link_aggregate", "aggregate": "count", "path": [""]}}, rels=rels), "requires 'path'")
    _raises(lambda: _derived(monkeypatch, {"n": {"kind": "link_aggregate", "aggregate": "count", "path": ["missing"]}}, rels=rels), "unknown relation")
    _raises(lambda: _derived(monkeypatch, {"n": {"kind": "link_aggregate", "aggregate": "sum", "path": ["orders"]}}, rels=rels, objects=objects), "requires a 'property'")
    _raises(lambda: _derived(
        monkeypatch, {"n": {"kind": "link_aggregate", "aggregate": "sum", "path": ["orders"], "property": "nope"}}, rels=rels, objects=objects
    ), "not a mapped property")
    # Reverse hop: start on Order and walk back to Customer, whose mapped property is read.
    customer = "hl:acme:main:object-type:Customer"
    _derived_from_order = dict(rels=rels, objects={customer: {"property_mapping": {"name": "name"}}})
    _patch_derived(monkeypatch, **_derived_from_order)
    _run(derived._validate_derived_properties(
        None,
        derived_properties={"label": {"kind": "link_aggregate", "aggregate": "min", "path": ["customer"], "property": "name"}},
        object_type_name="Order",
        tenant_id="acme",
    ))


def test_publishing_validate_reexports_the_checkers():
    assert publishing_validate._validate_property_formats is formats._validate_property_formats
    assert publishing_validate._validate_derived_properties is derived._validate_derived_properties
    assert callable(publishing_validate._validate_implements)
