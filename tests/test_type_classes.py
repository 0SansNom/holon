"""Unit tests for Foundry-style type class encoding (`kind:name`)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = REPO_ROOT / "services" / "knowledge"
LIBS = REPO_ROOT / "libs"


def _import_type_classes():
    sys.path.insert(0, str(LIBS))
    sys.path.insert(0, str(KNOWLEDGE_DIR))
    app = types.ModuleType("app")
    app.__path__ = [str(KNOWLEDGE_DIR / "app")]
    sys.modules.setdefault("app", app)
    ontology_pkg = types.ModuleType("app.ontology")
    ontology_pkg.__path__ = [str(KNOWLEDGE_DIR / "app" / "ontology")]
    sys.modules["app.ontology"] = ontology_pkg
    from app.ontology.type_classes import (  # noqa: E402
        find_property_with_type_class,
        has_type_class,
        normalize_type_class,
        normalize_type_classes,
        parse_type_class,
    )

    return (
        find_property_with_type_class,
        has_type_class,
        normalize_type_class,
        normalize_type_classes,
        parse_type_class,
    )


(
    find_property_with_type_class,
    has_type_class,
    normalize_type_class,
    normalize_type_classes,
    parse_type_class,
) = _import_type_classes()


def test_normalize_accepts_bare_and_kind_name() -> None:
    assert normalize_type_class("priority") == "priority"
    assert normalize_type_class("hubble:media_url") == "hubble:media_url"
    assert normalize_type_class("hierarchy:parent") == "hierarchy:parent"
    assert normalize_type_class("hubble-oe:hide-action") == "hubble-oe:hide-action"
    assert normalize_type_class("vertex:event_intent.danger") == "vertex:event_intent.danger"


def test_normalize_rejects_invalid() -> None:
    with pytest.raises(ValueError, match="type class"):
        normalize_type_class("Bad Class!")
    with pytest.raises(ValueError, match="type class"):
        normalize_type_class("")
    with pytest.raises(ValueError, match="type_classes"):
        normalize_type_classes("not-a-list")  # type: ignore[arg-type]


def test_parse_and_has_type_class() -> None:
    assert parse_type_class("priority") == ("custom", "priority")
    assert parse_type_class("hubble:icon") == ("hubble", "icon")
    assert has_type_class(["priority", "hubble-oe:hide-action"], "hubble-oe", "hide-action")
    assert not has_type_class(["hubble:icon"], "hubble", "media_url")


def test_find_property_with_type_class() -> None:
    props = {
        "title": {"type_classes": ["priority"]},
        "photoUrl": {"type_classes": ["hubble:media_url"]},
        "logo": {"type_classes": ["hubble:icon"]},
    }
    assert find_property_with_type_class(props, "hubble", "icon") == "logo"
    assert find_property_with_type_class(props, "hubble", "media_url") == "photoUrl"
    assert find_property_with_type_class(props, "hierarchy", "parent") is None
