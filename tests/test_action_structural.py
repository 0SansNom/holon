"""Unit tests for declarative Action structural edit validation."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = REPO_ROOT / "services" / "knowledge"
LIBS = REPO_ROOT / "libs"


def _import_structural():
    sys.path.insert(0, str(LIBS))
    sys.path.insert(0, str(KNOWLEDGE_DIR))
    app = types.ModuleType("app")
    app.__path__ = [str(KNOWLEDGE_DIR / "app")]
    sys.modules.setdefault("app", app)
    # holon_common is imported by action_structural for Principal typing only
    # at runtime of apply; validation itself needs no DB.
    from app.action_structural import (  # noqa: E402
        edit_kind,
        is_property_edit,
        property_edit_keys,
        split_result_for_response,
        validate_edit_declaration,
    )

    return (
        edit_kind,
        is_property_edit,
        property_edit_keys,
        split_result_for_response,
        validate_edit_declaration,
    )


(
    edit_kind,
    is_property_edit,
    property_edit_keys,
    split_result_for_response,
    validate_edit_declaration,
) = _import_structural()


def test_default_kind_is_modify_property() -> None:
    edit = {"property": "status", "source": "literal", "value": "open"}
    validate_edit_declaration(edit, parameter_names=set())
    assert edit_kind(edit) == "modify_property"
    assert is_property_edit(edit)


def test_modify_property_requires_declared_parameter() -> None:
    with pytest.raises(ValueError, match="undeclared parameter"):
        validate_edit_declaration(
            {"property": "status", "source": "parameter", "parameter_name": "missing"},
            parameter_names={"other"},
        )


def test_create_link_valid() -> None:
    validate_edit_declaration(
        {
            "kind": "create_link",
            "relation_type": "hasAccount",
            "source_from": "target_instance",
            "target_from": "parameter",
            "target_parameter": "accountId",
        },
        parameter_names={"accountId"},
    )


def test_create_object_and_delete_object() -> None:
    validate_edit_declaration(
        {
            "kind": "create_object",
            "object_type": "Note",
            "primary_key": {"source": "generate_uuid"},
            "properties": [{"property": "title", "source": "parameter", "parameter_name": "title"}],
        },
        parameter_names={"title"},
    )
    validate_edit_declaration(
        {"kind": "delete_object", "target_from": "target_instance"},
        parameter_names=set(),
    )


def test_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unknown kind"):
        validate_edit_declaration({"kind": "explode"}, parameter_names=set())


def test_response_split_and_property_keys() -> None:
    result = {"status": "reviewed", "__structural__": {"links": [], "objects": []}}
    assert split_result_for_response(result) == {"status": "reviewed"}
    assert property_edit_keys(result) == ["status"]
