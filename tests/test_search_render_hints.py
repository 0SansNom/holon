"""Unit tests for searchable/sortable property render hints in search indexing."""

from __future__ import annotations

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = REPO_ROOT / "services" / "knowledge"
LIBS = REPO_ROOT / "libs"

sys.path.insert(0, str(LIBS))
sys.path.insert(0, str(KNOWLEDGE_DIR))

# `app.search` imports httpx at module load; stub it for pure helper tests.
sys.modules.setdefault("httpx", types.ModuleType("httpx"))

from app.search import _searchable_columns, _sortable_prop_values  # noqa: E402


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
