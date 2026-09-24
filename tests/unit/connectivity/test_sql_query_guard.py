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
from app import sql_drivers  # noqa: E402


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


def test_mysql_file_helpers_are_rejected() -> None:
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT LOAD_FILE('/etc/passwd')", "mysql")
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT id FROM orders INTO OUTFILE '/tmp/x'", "mysql")


def test_mssql_file_helpers_are_rejected() -> None:
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT * FROM OPENROWSET('x', 'y')", "mssql")
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT xp_cmdshell('dir')", "mssql")


def test_default_ports_per_dialect() -> None:
    assert sql_drivers.default_port_for("postgres") == 5432
    assert sql_drivers.default_port_for("mysql") == 3306
    assert sql_drivers.default_port_for("mssql") == 1433


def test_normalize_dialect_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        sql_drivers.normalize_dialect("oracle")


def test_cursor_placeholders() -> None:
    assert sql_drivers.cursor_placeholder("postgres") == "$1"
    assert sql_drivers.cursor_placeholder("mysql") == "%s"
    assert sql_drivers.cursor_placeholder("mssql") == "?"
