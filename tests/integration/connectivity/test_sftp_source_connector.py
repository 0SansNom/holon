"""Tests for the no-code SFTP connector.

Real end-to-end against the `sftp` fixture (`atmoz/sftp`, test-fixtures
profile). Seed CSV is mounted at /home/holon/upload → remote path
`upload/suppliers.csv`. Hostname `sftp` is allowlisted in
HOLON_CONNECTOR_ALLOWED_HOSTS.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import CONNECTIVITY, IDENTITY, TENANT_ID, _unique_name

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "libs"))

from holon_sdk import HolonClient  # noqa: E402

client = HolonClient(identity_url=IDENTITY)
_request = client.request

SFTP_HOST = "sftp"
SFTP_PORT = 22
SFTP_USERNAME = "holon"
SFTP_PASSWORD = "holon12345"
REMOTE_FILE = "upload/suppliers.csv"
REMOTE_PREFIX = "upload"


@pytest.fixture(scope="session")
def jdoe_token() -> str:
    try:
        return client.token_for(f"hl:{TENANT_ID}:global:user:jdoe")
    except TimeoutError as exc:
        pytest.fail(str(exc))


def _register_sftp_connection(jdoe_token: str, name: str) -> None:
    status, connection = _request(
        "POST", f"{CONNECTIVITY}/sftp-connections", token=jdoe_token,
        body={
            "name": name,
            "host": SFTP_HOST,
            "port": SFTP_PORT,
            "username": SFTP_USERNAME,
            "password": SFTP_PASSWORD,
        },
    )
    assert status == 200, connection
    assert "password" not in connection, connection
    assert connection["has_password"] is True, connection


def test_remote_path_mode_syncs_seeded_suppliers_csv(jdoe_token: str) -> None:
    connection_name = _unique_name("sftp_conn")
    _register_sftp_connection(jdoe_token, connection_name)

    source_name = _unique_name("sftp_suppliers")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sftp-sources", token=jdoe_token,
        body={
            "name": source_name,
            "connection_name": connection_name,
            "remote_path": REMOTE_FILE,
            "format": "csv",
        },
    )
    assert status == 200, registration
    assert registration["remote_path"] == REMOTE_FILE, registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, result
    assert result["row_count"] > 0, result


def test_remote_prefix_mode_lists_and_reads(jdoe_token: str) -> None:
    connection_name = _unique_name("sftp_conn_prefix")
    _register_sftp_connection(jdoe_token, connection_name)

    source_name = _unique_name("sftp_prefix")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sftp-sources", token=jdoe_token,
        body={
            "name": source_name,
            "connection_name": connection_name,
            "remote_prefix": REMOTE_PREFIX,
            "format": "csv",
        },
    )
    assert status == 200, registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, result
    assert result["row_count"] > 0, result


def test_path_traversal_is_rejected(jdoe_token: str) -> None:
    connection_name = _unique_name("sftp_conn_bad")
    _register_sftp_connection(jdoe_token, connection_name)

    status, body = _request(
        "POST", f"{CONNECTIVITY}/sftp-sources", token=jdoe_token,
        body={
            "name": _unique_name("sftp_bad"),
            "connection_name": connection_name,
            "remote_path": "../etc/passwd",
            "format": "csv",
        },
    )
    assert status == 400, body


def test_deleting_connection_in_use_is_409(jdoe_token: str) -> None:
    connection_name = _unique_name("sftp_conn_del")
    _register_sftp_connection(jdoe_token, connection_name)

    source_name = _unique_name("sftp_del")
    status, _ = _request(
        "POST", f"{CONNECTIVITY}/sftp-sources", token=jdoe_token,
        body={
            "name": source_name,
            "connection_name": connection_name,
            "remote_path": REMOTE_FILE,
            "format": "csv",
        },
    )
    assert status == 200

    status, body = _request("DELETE", f"{CONNECTIVITY}/sftp-connections/{connection_name}", token=jdoe_token)
    assert status == 409, body
    assert source_name in body["detail"], body
