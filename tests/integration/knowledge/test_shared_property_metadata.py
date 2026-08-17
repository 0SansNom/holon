"""Tests for Shared Property Metadata."""

from __future__ import annotations

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_DIR = REPO_ROOT / "services" / "knowledge"
LIBS = REPO_ROOT / "libs"


def _import_spt_module():
    sys.path.insert(0, str(LIBS))
    sys.path.insert(0, str(KNOWLEDGE_DIR))
    app = types.ModuleType("app")
    app.__path__ = [str(KNOWLEDGE_DIR / "app")]
    sys.modules.setdefault("app", app)
    ontology_pkg = types.ModuleType("app.ontology")
    ontology_pkg.__path__ = [str(KNOWLEDGE_DIR / "app" / "ontology")]
    sys.modules.setdefault("app.ontology", ontology_pkg)
    value_types = types.ModuleType("app.ontology.value_types")
    sys.modules["app.ontology.value_types"] = value_types
    from app.ontology import shared_property_types as spt  # noqa: WPS433

    return spt


def test_property_types_reference_spt_detects_top_level_and_nested() -> None:
    spt = _import_spt_module()
    api = "StartDate"
    assert spt._property_types_reference_spt(
        {"startDate": {"kind": "shared_property_type", "shared_property_type": api}},
        api,
    )
    assert spt._property_types_reference_spt(
        {
            "address": {
                "kind": "struct",
                "properties": {"city": {"kind": "shared_property_type", "shared_property_type": api}},
            }
        },
        api,
    )
    assert spt._property_types_reference_spt(
        {
            "tags": {
                "kind": "array",
                "element": {
                    "kind": "struct",
                    "properties": {"label": {"kind": "shared_property_type", "shared_property_type": api}},
                },
            }
        },
        api,
    )
    assert not spt._property_types_reference_spt(
        {"startDate": {"kind": "value_type", "value_type": "Date"}},
        api,
    )


def test_normalize_metadata_rejects_bad_visibility() -> None:
    spt = _import_spt_module()
    try:
        spt._normalize_metadata(visibility="loud")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "visibility" in str(exc)


def test_normalize_aliases_dedupes_and_trims() -> None:
    spt = _import_spt_module()
    assert spt._normalize_aliases(["  Hire Date ", "hire date", "Onboarding", ""]) == [
        "Hire Date",
        "Onboarding",
    ]
    try:
        spt._normalize_aliases("nope")  # type: ignore[arg-type]
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "aliases" in str(exc)


def test_shared_property_type_urn() -> None:
    spt = _import_spt_module()
    assert spt.shared_property_type_urn("acme", "startDate") == "hl:acme:global:shared-property-type:startDate"


def test_local_rule_from_spt_value_and_struct() -> None:
    spt = _import_spt_module()
    assert spt._local_rule_from_spt(
        {"api_name": "startDate", "value_type": "Date", "visibility": "prominent", "render_hints": ["sortable"]}
    ) == {
        "kind": "value_type",
        "value_type": "Date",
        "visibility": "prominent",
        "render_hints": ["sortable"],
    }
    assert spt._local_rule_from_spt(
        {
            "api_name": "Address",
            "struct_properties": {"city": {"kind": "value_type", "value_type": "string"}},
            "type_classes": ["geo"],
        }
    ) == {
        "kind": "struct",
        "properties": {"city": {"kind": "value_type", "value_type": "string"}},
        "type_classes": ["geo"],
    }


def test_detach_spt_from_property_types_top_level_and_nested() -> None:
    spt = _import_spt_module()
    api = "StartDate"
    shared = {"api_name": api, "value_type": "Date", "visibility": "prominent"}
    rewritten = spt._detach_spt_from_property_types(
        {
            "startDate": {"kind": "shared_property_type", "shared_property_type": api},
            "other": {"kind": "value_type", "value_type": "string"},
            "address": {
                "kind": "struct",
                "properties": {
                    "city": {"kind": "shared_property_type", "shared_property_type": api, "main_field": True},
                    "zip": {"kind": "value_type", "value_type": "string"},
                },
            },
            "tags": {
                "kind": "array",
                "element": {"kind": "shared_property_type", "shared_property_type": api},
            },
        },
        api,
        shared,
    )
    assert rewritten["startDate"] == {
        "kind": "value_type",
        "value_type": "Date",
        "visibility": "prominent",
    }
    assert rewritten["other"] == {"kind": "value_type", "value_type": "string"}
    assert rewritten["address"]["properties"]["city"] == {
        "kind": "value_type",
        "value_type": "Date",
        "main_field": True,
    }
    assert rewritten["tags"]["element"] == {
        "kind": "value_type",
        "value_type": "Date",
        "visibility": "prominent",
    }
