"""Tests for the no-code SQL connector (Postgres wire protocol).

Real end-to-end, no new fixture infra: `source_erp` is already a live
Postgres database in the compose stack (`HOLON_SOURCE_DB_URL`, seeded by
`make seed`), currently only read by the PostgresCustomersPlugin/
PostgresOrdersPlugin fixture plugins. These tests register it as a
`sql_connection` and read real tables/queries through the no-code path.
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

# Connectivity's own network view of Postgres, not the test runner's.
SOURCE_ERP_HOST = "postgres"
SOURCE_ERP_PORT = 5432
SOURCE_ERP_DATABASE = "source_erp"
SOURCE_ERP_USERNAME = "holon"
SOURCE_ERP_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "holon12345")


@pytest.fixture(scope="session")
def jdoe_token() -> str:
    try:
        return client.token_for(f"hl:{TENANT_ID}:global:user:jdoe")
    except TimeoutError as exc:
        pytest.fail(str(exc))


def _register_source_erp_connection(jdoe_token: str, name: str) -> None:
    status, connection = _request(
        "POST", f"{CONNECTIVITY}/sql-connections", token=jdoe_token,
        body={
            "name": name,
            "host": SOURCE_ERP_HOST,
            "port": SOURCE_ERP_PORT,
            "database": SOURCE_ERP_DATABASE,
            "username": SOURCE_ERP_USERNAME,
            "password": SOURCE_ERP_PASSWORD,
        },
    )
    assert status == 200, connection
    assert "password" not in connection, connection  # never echoed back
    assert connection["has_password"] is True, connection


def test_table_mode_syncs_real_rows_from_source_erp(jdoe_token: str) -> None:
    connection_name = _unique_name("source_erp_conn")
    _register_source_erp_connection(jdoe_token, connection_name)

    source_name = _unique_name("sql_orders")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sql-sources", token=jdoe_token,
        body={"name": source_name, "connection_name": connection_name, "table_name": "orders"},
    )
    assert status == 200, registration
    assert registration["table_name"] == "orders", registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, result
    assert result["row_count"] > 0, result


def test_query_mode_syncs_a_filtered_select_sharing_the_same_connection(jdoe_token: str) -> None:
    connection_name = _unique_name("source_erp_conn_shared")
    _register_source_erp_connection(jdoe_token, connection_name)

    table_source = _unique_name("sql_orders_shared")
    status, _ = _request(
        "POST", f"{CONNECTIVITY}/sql-sources", token=jdoe_token,
        body={"name": table_source, "connection_name": connection_name, "table_name": "orders"},
    )
    assert status == 200

    query_source = _unique_name("sql_big_orders")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sql-sources", token=jdoe_token,
        body={
            "name": query_source,
            "connection_name": connection_name,
            "query": "SELECT id, product, amount FROM orders WHERE amount > 0",
        },
    )
    assert status == 200, registration
    assert registration["query"], registration
    assert registration["table_name"] is None, registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": query_source})
    assert status == 200, result
    assert result["row_count"] > 0, result


def test_non_select_query_is_rejected(jdoe_token: str) -> None:
    connection_name = _unique_name("source_erp_conn_bad")
    _register_source_erp_connection(jdoe_token, connection_name)

    status, body = _request(
        "POST", f"{CONNECTIVITY}/sql-sources", token=jdoe_token,
        body={
            "name": _unique_name("sql_bad_query"),
            "connection_name": connection_name,
            "query": "DELETE FROM orders",
        },
    )
    assert status == 400, body


def test_malicious_table_name_is_rejected(jdoe_token: str) -> None:
    connection_name = _unique_name("source_erp_conn_inj")
    _register_source_erp_connection(jdoe_token, connection_name)

    status, body = _request(
        "POST", f"{CONNECTIVITY}/sql-sources", token=jdoe_token,
        body={
            "name": _unique_name("sql_injection_attempt"),
            "connection_name": connection_name,
            "table_name": 'orders"; DROP TABLE customers;--',
        },
    )
    assert status == 400, body


def test_both_table_name_and_query_is_rejected(jdoe_token: str) -> None:
    connection_name = _unique_name("source_erp_conn_both")
    _register_source_erp_connection(jdoe_token, connection_name)

    status, body = _request(
        "POST", f"{CONNECTIVITY}/sql-sources", token=jdoe_token,
        body={
            "name": _unique_name("sql_both"),
            "connection_name": connection_name,
            "table_name": "orders",
            "query": "SELECT 1",
        },
    )
    assert status == 400, body


def test_registering_under_a_plugin_claimed_dataset_is_rejected(jdoe_token: str) -> None:
    connection_name = _unique_name("source_erp_conn_conflict")
    _register_source_erp_connection(jdoe_token, connection_name)

    status, body = _request(
        "POST", f"{CONNECTIVITY}/sql-sources", token=jdoe_token,
        body={"name": "customers", "connection_name": connection_name, "table_name": "customers"},
    )
    assert status == 409, body
    assert "already claimed" in body["detail"], body


def test_incremental_cursor_only_fetches_new_rows(jdoe_token: str) -> None:
    connection_name = _unique_name("source_erp_conn_cursor")
    _register_source_erp_connection(jdoe_token, connection_name)

    source_name = _unique_name("sql_orders_incremental")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sql-sources", token=jdoe_token,
        body={
            "name": source_name,
            "connection_name": connection_name,
            "table_name": "orders",
            "cursor_property": "id",
        },
    )
    assert status == 200, registration

    status, first = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, first
    assert first["row_count"] > 0, first
    
    status, second = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, second
    assert second["row_count"] == first["row_count"], second


def test_disable_then_sync_is_conflict_then_enable_restores_it(jdoe_token: str) -> None:
    connection_name = _unique_name("source_erp_conn_toggle")
    _register_source_erp_connection(jdoe_token, connection_name)

    source_name = _unique_name("sql_orders_toggle")
    status, _ = _request(
        "POST", f"{CONNECTIVITY}/sql-sources", token=jdoe_token,
        body={"name": source_name, "connection_name": connection_name, "table_name": "orders"},
    )
    assert status == 200

    status, body = _request("POST", f"{CONNECTIVITY}/sql-sources/{source_name}/disable", token=jdoe_token)
    assert status == 200, body
    assert body["status"] == "disabled", body

    status, body = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 409, body

    status, body = _request("POST", f"{CONNECTIVITY}/sql-sources/{source_name}/enable", token=jdoe_token)
    assert status == 200, body
    assert body["status"] == "active", body

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, result


def test_deleting_a_connection_still_in_use_is_409(jdoe_token: str) -> None:
    connection_name = _unique_name("source_erp_conn_delete")
    _register_source_erp_connection(jdoe_token, connection_name)

    source_name = _unique_name("sql_orders_delete")
    status, _ = _request(
        "POST", f"{CONNECTIVITY}/sql-sources", token=jdoe_token,
        body={"name": source_name, "connection_name": connection_name, "table_name": "orders"},
    )
    assert status == 200

    status, body = _request("DELETE", f"{CONNECTIVITY}/sql-connections/{connection_name}", token=jdoe_token)
    assert status == 409, body
    assert source_name in body["detail"], body

    status, body = _request("DELETE", f"{CONNECTIVITY}/sql-sources/{source_name}", token=jdoe_token)
    assert status == 200, body

    status, body = _request("DELETE", f"{CONNECTIVITY}/sql-connections/{connection_name}", token=jdoe_token)
    assert status == 200, body
