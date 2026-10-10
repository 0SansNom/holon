"""Unit tests for holon_osdk schema parsing (no HTTP)."""

from __future__ import annotations

import pytest

from holon_osdk.schema import PropertyType, _parse_property_type


def test_parse_value_type() -> None:
    pt = _parse_property_type({"kind": "value_type", "value_type": "String"})
    assert pt == PropertyType(kind="value_type", value_type="String")


def test_parse_shared_property_type() -> None:
    pt = _parse_property_type({"kind": "shared_property_type", "shared_property_type": "Email"})
    assert pt.kind == "shared_property_type"
    assert pt.shared_property_type == "Email"


def test_parse_struct_and_array() -> None:
    pt = _parse_property_type(
        {
            "kind": "array",
            "element": {
                "kind": "struct",
                "properties": {
                    "label": {"kind": "value_type", "value_type": "String"},
                    "score": {"kind": "value_type", "value_type": "Double"},
                },
            },
        }
    )
    assert pt.kind == "array"
    assert pt.element is not None
    assert pt.element.kind == "struct"
    assert set(pt.element.properties) == {"label", "score"}
    assert pt.element.properties["label"].value_type == "String"


def test_parse_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown property_types kind"):
        _parse_property_type({"kind": "map"})
