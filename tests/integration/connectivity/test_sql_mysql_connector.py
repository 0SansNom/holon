"""Integration tests for the MySQL SQL dialect connector.

Requires the `mysql` service from docker-compose profile `test-fixtures`
(started by `make seed`). Mirrors test_sql_source_connector.py against
Postgres source_erp.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from conftest import CONNECTIVITY, IDENTITY, TENANT_ID, _unique_name

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "libs"))

from holon_sdk import HolonClient  # noqa: E402

client = HolonClient(identity_url=IDENTITY)
_request = client.request

MYSQL_HOST = "mysql"
MYSQL_PORT = 3306
MYSQL_DATABASE = "source_erp"
MYSQL_USERNAME = "holon"
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "holon12345")


@pytest.fixture(scope="session")
def jdoe_token() -> str:
    try:
        return client.token_for(f"hl:{TENANT_ID}:global:user:jdoe")
    except TimeoutError as exc:
        pytest.fail(str(exc))


def _register_mysql_connection(jdoe_token: str, name: str) -> None:
    status, connection = _request(
        "POST", f"{CONNECTIVITY}/sql-connections", token=jdoe_token,
        body={
            "name": name,
            "dialect": "mysql",
            "host": MYSQL_HOST,
            "port": MYSQL_PORT,
            "database": MYSQL_DATABASE,
            "username": MYSQL_USERNAME,
            "password": MYSQL_PASSWORD,
        },
    )
    assert status == 200, connection
    assert connection["dialect"] == "mysql", connection
    assert "password" not in connection, connection
    assert connection["has_password"] is True, connection


def test_mysql_table_mode_syncs_real_rows(jdoe_token: str) -> None:
    connection_name = _unique_name("mysql_source_erp_conn")
    _register_mysql_connection(jdoe_token, connection_name)

    source_name = _unique_name("mysql_sql_orders")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sql-sources", token=jdoe_token,
        body={"name": source_name, "connection_name": connection_name, "table_name": "orders"},
    )
    assert status == 200, registration
    assert registration["table_name"] == "orders", registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, result
    assert result["row_count"] >= 1, result


def test_mysql_query_mode_syncs_filtered_select(jdoe_token: str) -> None:
    connection_name = _unique_name("mysql_source_erp_q")
    _register_mysql_connection(jdoe_token, connection_name)

    source_name = _unique_name("mysql_sql_orders_q")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sql-sources", token=jdoe_token,
        body={
            "name": source_name,
            "connection_name": connection_name,
            "query": "SELECT id, product, amount FROM orders WHERE status = 'delivered'",
        },
    )
    assert status == 200, registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, result
    assert result["row_count"] >= 1, result
