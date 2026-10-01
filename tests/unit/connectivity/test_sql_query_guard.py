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
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT SLEEP(5)", "mysql")


def test_mssql_file_helpers_are_rejected() -> None:
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT * FROM OPENROWSET('x', 'y')", "mssql")
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT xp_cmdshell('dir')", "mssql")
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT * FROM OPENQUERY(linked, 'SELECT 1')", "mssql")


def test_snowflake_stage_and_system_helpers_are_rejected() -> None:
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT $1 FROM @landing/data.csv.gz", "snowflake")
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT * FROM TABLE(DIRECTORY(@landing))", "snowflake")
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT SYSTEM$CANCEL_ALL_QUERIES(CURRENT_SESSION())", "snowflake")
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT GET_PRESIGNED_URL(@landing, 'a.csv')", "snowflake")
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT BUILD_SCOPED_FILE_URL(@landing, 'a.csv')", "snowflake")
    # Ordinary warehouse reads stay allowed (and email-like @ in literals is fine).
    _require_select_only("SELECT id FROM analytics.orders WHERE email = 'a@b.com'", "snowflake")


def test_default_ports_per_dialect() -> None:
    assert sql_drivers.default_port_for("postgres") == 5432
    assert sql_drivers.default_port_for("mysql") == 3306
    assert sql_drivers.default_port_for("mssql") == 1433
    assert sql_drivers.default_port_for("snowflake") == 443
    assert sql_drivers.default_port_for("alloydb") == 5432
    assert sql_drivers.default_port_for("cockroachdb") == 26257
    assert sql_drivers.default_port_for("enterprisedb") == 5444
    assert sql_drivers.default_port_for("greenplum") == 5432
    assert sql_drivers.default_port_for("singlestore") == 3306
    assert sql_drivers.default_port_for("mariadb") == 3306
    assert sql_drivers.default_port_for("azure_synapse") == 1433
    assert sql_drivers.default_port_for("azure_synapse_serverless") == 1433
    assert sql_drivers.default_port_for("Azure-Synapse") == 1433


def test_default_tls_per_dialect() -> None:
    assert sql_drivers.default_tls_for("alloydb") is True
    assert sql_drivers.default_tls_for("cockroachdb") is True
    assert sql_drivers.default_tls_for("azure_synapse") is True
    assert sql_drivers.default_tls_for("azure_synapse_serverless") is True
    assert sql_drivers.default_tls_for("postgres") is False
    assert sql_drivers.default_tls_for("singlestore") is False
    assert sql_drivers.default_tls_for("mariadb") is False
    assert sql_drivers.resolve_use_tls("alloydb", None) is True
    assert sql_drivers.resolve_use_tls("alloydb", False) is False
    assert sql_drivers.resolve_use_tls("snowflake", True) is False


def test_normalize_dialect_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        sql_drivers.normalize_dialect("oracle")
    assert sql_drivers.normalize_dialect("snowflake") == "snowflake"


def test_compatible_dialects_map_to_wire_drivers() -> None:
    cases = {
        "alloydb": "postgres",
        "cockroachdb": "postgres",
        "enterprisedb": "postgres",
        "greenplum": "postgres",
        "singlestore": "mysql",
        "mariadb": "mysql",
        "azure_synapse": "mssql",
        "azure_synapse_serverless": "mssql",
        "Azure Synapse": "mssql",
    }
    for name, wire in cases.items():
        assert sql_drivers.normalize_dialect(name) == name.strip().lower().replace("-", "_").replace(" ", "_")
        assert sql_drivers.wire_dialect(name) == wire
    assert sql_drivers.cursor_placeholder("cockroachdb") == "$1"
    assert sql_drivers.cursor_placeholder("singlestore") == "%s"
    assert sql_drivers.cursor_placeholder("mariadb") == "%s"
    assert sql_drivers.cursor_placeholder("azure_synapse") == "?"
    assert sql_drivers.cursor_placeholder("azure_synapse_serverless") == "?"


def test_compatible_dialect_query_guards_follow_wire() -> None:
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT LOAD_FILE('/etc/passwd')", "singlestore")
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT LOAD_FILE('/etc/passwd')", "mariadb")
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT * FROM OPENROWSET('x', 'y')", "azure_synapse")
    _require_select_only(
        "SELECT * FROM OPENROWSET(BULK 'https://acct.blob.core.windows.net/c/f.parquet')",
        "azure_synapse_serverless",
    )
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT xp_cmdshell('dir')", "azure_synapse_serverless")
    with pytest.raises(SourceConfigError):
        _require_select_only("SELECT * FROM OPENQUERY(linked, 'SELECT 1')", "azure_synapse_serverless")
    _require_select_only("SELECT id FROM orders", "cockroachdb")


def test_cursor_placeholders() -> None:
    assert sql_drivers.cursor_placeholder("postgres") == "$1"
    assert sql_drivers.cursor_placeholder("mysql") == "%s"
    assert sql_drivers.cursor_placeholder("mssql") == "?"
    assert sql_drivers.cursor_placeholder("snowflake") == "%s"
