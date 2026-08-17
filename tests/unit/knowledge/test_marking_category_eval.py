"""Unit tests for marking category evaluation (no stack required)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
KNOWLEDGE_DIR = REPO / "services" / "knowledge"
LIBS = REPO / "libs"


def _import_eval():
    sys.path.insert(0, str(LIBS))
    sys.path.insert(0, str(KNOWLEDGE_DIR))
    app = types.ModuleType("app")
    app.__path__ = [str(KNOWLEDGE_DIR / "app")]
    sys.modules.setdefault("app", app)
    ontology_pkg = types.ModuleType("app.ontology")
    ontology_pkg.__path__ = [str(KNOWLEDGE_DIR / "app" / "ontology")]
    sys.modules["app.ontology"] = ontology_pkg
    from app.ontology.markings import category_groups_satisfied  # noqa: E402

    return category_groups_satisfied


category_groups_satisfied = _import_eval()


def test_conjunctive_requires_all() -> None:
    meta = [
        {"name": "a", "category_id": "c1", "category_type": "CONJUNCTIVE"},
        {"name": "b", "category_id": "c1", "category_type": "CONJUNCTIVE"},
    ]
    assert category_groups_satisfied(meta, {"a": True, "b": True}) is True
    assert category_groups_satisfied(meta, {"a": True, "b": False}) is False


def test_disjunctive_requires_any() -> None:
    meta = [
        {"name": "a", "category_id": "c1", "category_type": "DISJUNCTIVE"},
        {"name": "b", "category_id": "c1", "category_type": "DISJUNCTIVE"},
    ]
    assert category_groups_satisfied(meta, {"a": True, "b": False}) is True
    assert category_groups_satisfied(meta, {"a": False, "b": False}) is False


def test_categories_and_together() -> None:
    meta = [
        {"name": "pii", "category_id": "conj", "category_type": "CONJUNCTIVE"},
        {"name": "eu", "category_id": "disj", "category_type": "DISJUNCTIVE"},
        {"name": "us", "category_id": "disj", "category_type": "DISJUNCTIVE"},
    ]
    assert category_groups_satisfied(meta, {"pii": True, "eu": True, "us": False}) is True
    assert category_groups_satisfied(meta, {"pii": False, "eu": True, "us": True}) is False
    assert category_groups_satisfied(meta, {"pii": True, "eu": False, "us": False}) is False
