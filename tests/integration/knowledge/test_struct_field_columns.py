"""Unit tests for struct assemble (JSON column + per-field columns)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_DIR = REPO_ROOT / "services" / "knowledge"
LIBS = REPO_ROOT / "libs"

sys.path.insert(0, str(LIBS))
sys.path.insert(0, str(KNOWLEDGE_DIR))

from app.struct_values import assemble_struct_value  # noqa: E402


def test_assemble_struct_overlays_field_columns() -> None:
    rule = {
        "kind": "struct",
        "properties": {
            "city": {"kind": "value_type", "value_type": "String", "column": "city_col"},
            "zip": {"kind": "value_type", "value_type": "String", "column": "zip_col"},
            "country": {"kind": "value_type", "value_type": "String"},
        },
    }
    row = {
        "address_json": '{"city": "Old", "country": "FR"}',
        "city_col": "Paris",
        "zip_col": "75001",
    }
    assert assemble_struct_value(rule, row, "address_json") == {
        "city": "Paris",
        "country": "FR",
        "zip": "75001",
    }


def test_assemble_struct_drops_undeclared_json_keys() -> None:
    rule = {
        "kind": "struct",
        "properties": {
            "city": {"kind": "value_type", "value_type": "String"},
        },
    }
    row = {"address_json": '{"city": "Lyon", "country": "FR", "extra": 1}'}
    assert assemble_struct_value(rule, row, "address_json") == {"city": "Lyon"}


def test_assemble_struct_field_columns_only() -> None:
    rule = {
        "kind": "struct",
        "properties": {
            "city": {"kind": "value_type", "value_type": "String", "column": "city_col"},
        },
    }
    row = {"city_col": "Lyon"}
    assert assemble_struct_value(rule, row, "address_json") == {"city": "Lyon"}
