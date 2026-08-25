"""Tests for the SQL source read-only query guard."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.modules.setdefault("asyncpg", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app.sql_source_registry import SourceConfigError, _require_select_only  # noqa: E402


def test_select_and_with_are_allowed() -> None:
    _require_select_only("SELECT id FROM orders")
    _require_select_only("WITH c AS (SELECT 1 AS id) SELECT * FROM c")


def test_into_column_alias_is_allowed() -> None:
    _require_select_only('SELECT x AS "into_col" FROM orders')
    _require_select_only("SELECT copy FROM orders")


def test_write_and_file_helpers_are_rejected() -> None:
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT pg_read_binary_file('/etc/passwd')")
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT lo_get(1)")
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT * FROM orders FOR UPDATE")
    with pytest.raises(SourceConfigError):
        _require_select_only("INSERT INTO orders VALUES (1)")
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT * INTO tmp FROM orders")
