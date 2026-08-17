"""Unit tests for Action parameter Form defaults validation."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_DIR = REPO_ROOT / "services" / "knowledge"
LIBS = REPO_ROOT / "libs"


def _import_validate():
    sys.path.insert(0, str(LIBS))
    sys.path.insert(0, str(KNOWLEDGE_DIR))
    app = types.ModuleType("app")
    app.__path__ = [str(KNOWLEDGE_DIR / "app")]
    sys.modules.setdefault("app", app)
    ontology_pkg = types.ModuleType("app.ontology")
    ontology_pkg.__path__ = [str(KNOWLEDGE_DIR / "app" / "ontology")]
    sys.modules["app.ontology"] = ontology_pkg
    # Minimal stubs for action_types imports
    tc = types.ModuleType("app.ontology.type_classes")
    tc.normalize_type_classes = lambda x: list(x or [])
    sys.modules["app.ontology.type_classes"] = tc
    life = types.ModuleType("app.ontology.lifecycle")
    life.normalize_deprecation_metadata = lambda *a, **k: {
        "lifecycle_status": "experimental",
        "deprecation_reason": None,
        "deprecation_deadline": None,
        "replacement_urn": None,
    }
    sys.modules["app.ontology.lifecycle"] = life
    structural = types.ModuleType("app.action_structural")
    structural.validate_edit_declaration = lambda *a, **k: None
    structural.is_property_edit = lambda e: (e.get("kind") or "modify_property") == "modify_property"
    sys.modules["app.action_structural"] = structural

    from app.ontology.action_types import validate_parameter_default  # noqa: E402

    return validate_parameter_default


validate_parameter_default = _import_validate()


def test_static_and_current_object() -> None:
    validate_parameter_default(
        {"name": "status", "default": {"kind": "static", "value": "open"}},
        earlier_object_reference_names=set(),
    )
    validate_parameter_default(
        {"name": "target", "default": {"kind": "current_object"}},
        earlier_object_reference_names=set(),
    )


def test_object_property_requires_earlier_ref() -> None:
    validate_parameter_default(
        {
            "name": "hours",
            "default": {"kind": "object_property", "object": "current", "property": "flightHours"},
        },
        earlier_object_reference_names=set(),
    )
    with pytest.raises(ValueError, match="earlier"):
        validate_parameter_default(
            {
                "name": "hours",
                "default": {"kind": "object_property", "object": "planeId", "property": "flightHours"},
            },
            earlier_object_reference_names=set(),
        )
    validate_parameter_default(
        {
            "name": "hours",
            "default": {"kind": "object_property", "object": "planeId", "property": "flightHours"},
        },
        earlier_object_reference_names={"planeId"},
    )


def test_rejects_bad_default() -> None:
    with pytest.raises(ValueError, match="unknown default.kind"):
        validate_parameter_default(
            {"name": "x", "default": {"kind": "magic"}},
            earlier_object_reference_names=set(),
        )
    with pytest.raises(ValueError, match="requires 'value'"):
        validate_parameter_default(
            {"name": "x", "default": {"kind": "static"}},
            earlier_object_reference_names=set(),
        )
