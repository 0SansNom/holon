"""Unit tests for render_hints + lifecycle deprecation helpers."""

from __future__ import annotations

import sys
import types
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = REPO_ROOT / "services" / "knowledge"
LIBS = REPO_ROOT / "libs"


def _import_helpers():
    sys.path.insert(0, str(LIBS))
    sys.path.insert(0, str(KNOWLEDGE_DIR))
    app = types.ModuleType("app")
    app.__path__ = [str(KNOWLEDGE_DIR / "app")]
    sys.modules.setdefault("app", app)
    ontology_pkg = types.ModuleType("app.ontology")
    ontology_pkg.__path__ = [str(KNOWLEDGE_DIR / "app" / "ontology")]
    sys.modules["app.ontology"] = ontology_pkg
    from app.ontology.render_hints import normalize_render_hints  # noqa: E402
    from app.ontology.lifecycle import normalize_deprecation_metadata  # noqa: E402

    return normalize_render_hints, normalize_deprecation_metadata


normalize_render_hints, normalize_deprecation_metadata = _import_helpers()


def test_sortable_requires_searchable() -> None:
    with pytest.raises(ValueError, match="require 'searchable'"):
        normalize_render_hints(["sortable"])
    with pytest.raises(ValueError, match="require 'searchable'"):
        normalize_render_hints(["selectable", "identifier"])
    assert normalize_render_hints(["searchable", "sortable", "selectable"]) == [
        "searchable",
        "sortable",
        "selectable",
    ]


def test_identifier_alone_ok() -> None:
    assert normalize_render_hints(["identifier"]) == ["identifier"]
    assert normalize_render_hints(None, default=["searchable"]) == ["searchable"]
    assert normalize_render_hints([]) == []


def test_deprecate_requires_reason_and_deadline() -> None:
    with pytest.raises(ValueError, match="deprecation_reason"):
        normalize_deprecation_metadata("deprecated", deprecation_deadline="2026-12-01")
    with pytest.raises(ValueError, match="deprecation_deadline"):
        normalize_deprecation_metadata("deprecated", deprecation_reason="old")
    meta = normalize_deprecation_metadata(
        "deprecated",
        deprecation_reason="superseded",
        deprecation_deadline="2026-12-31",
        replacement_urn="hl:t:w:object-type:New",
    )
    assert meta["deprecation_deadline"] == date(2026, 12, 31)
    assert meta["replacement_urn"] == "hl:t:w:object-type:New"


def test_keywords_and_long_text_allowed() -> None:
    assert normalize_render_hints(["searchable", "keywords", "long_text"]) == [
        "searchable",
        "keywords",
        "long_text",
    ]


def test_non_deprecated_clears_metadata() -> None:
    meta = normalize_deprecation_metadata(
        "active",
        deprecation_reason="stale",
        deprecation_deadline="2026-01-01",
        replacement_urn="x",
    )
    assert meta == {
        "lifecycle_status": "active",
        "deprecation_reason": None,
        "deprecation_deadline": None,
        "replacement_urn": None,
    }


def test_example_and_promoted_lifecycle() -> None:
    from app.ontology.lifecycle import assert_lifecycle_for_target, normalize_lifecycle_status

    assert normalize_lifecycle_status("example") == "example"
    assert normalize_lifecycle_status("promoted") == "promoted"
    assert assert_lifecycle_for_target("promoted", target="object_type") == "promoted"
    with pytest.raises(ValueError, match="only valid on ObjectType"):
        assert_lifecycle_for_target("promoted", target="property")
    assert assert_lifecycle_for_target("example", target="property") == "example"


def test_low_cardinality_requires_searchable() -> None:
    with pytest.raises(ValueError, match="require 'searchable'"):
        normalize_render_hints(["low_cardinality"])
    assert normalize_render_hints(["searchable", "low_cardinality", "enable_regex_queries"]) == [
        "searchable",
        "low_cardinality",
        "enable_regex_queries",
    ]
