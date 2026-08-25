"""Tests for the no-code object storage connector (S3-compatible).

Real end-to-end against `source-s3`, a dedicated fake-external MinIO in
the compose stack (`docker-compose.yml`, `test-fixtures` profile) —
deliberately a *different* container/hostname from the platform's own
`minio` (the warehouse/catalog store): `holon_common.connector_safety`
blocks the `minio` hostname outright, since a tenant connector must
never be able to reach Holon's own bucket (same SSRF reasoning that
already applies to the SQL connector's `assert_connector_host`). `make
seed` copies `landing/suppliers.csv` into `source-s3`'s `tenant-landing`
bucket via the `source-s3-seed` fixture service.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.fs as pafs
import pytest
from conftest import CONNECTIVITY, IDENTITY, TENANT_ID, _unique_name

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "libs"))

from holon_sdk import HolonClient  # noqa: E402

client = HolonClient(identity_url=IDENTITY)
_request = client.request

# Connectivity's own network view of source-s3, not the test runner's.
# `HOLON_CONNECTOR_ALLOWED_HOSTS` (docker-compose.yml) allowlists this
# host specifically — see the module docstring.
MINIO_ENDPOINT = "http://source-s3:9000"
# The test runner's own view — source-s3's published host port (same
# localhost-vs-compose-hostname split test_bitemporal_history.py already
# uses for source_erp: registered connections use the connector's network
# view, direct test-side writes use the host's).
HOST_MINIO_ENDPOINT = "http://localhost:9500"
MINIO_ACCESS_KEY_ID = "source"
MINIO_SECRET_ACCESS_KEY = "source12345"
BUCKET = "tenant-landing"


@pytest.fixture(scope="session")
def jdoe_token() -> str:
    try:
        return client.token_for(f"hl:{TENANT_ID}:global:user:jdoe")
    except TimeoutError as exc:
        pytest.fail(str(exc))


def _register_minio_connection(jdoe_token: str, name: str) -> None:
    status, connection = _request(
        "POST", f"{CONNECTIVITY}/object-connections", token=jdoe_token,
        body={
            "name": name,
            "endpoint": MINIO_ENDPOINT,
            "access_key_id": MINIO_ACCESS_KEY_ID,
            "secret_access_key": MINIO_SECRET_ACCESS_KEY,
            "path_style": True,
        },
    )
    assert status == 200, connection
    assert "secret_access_key" not in connection, connection  # never echoed back
    assert connection["has_secret_access_key"] is True, connection


def _write_csv_object(key: str, rows: list[dict]) -> None:
    """Test-side write, mirroring how the SQL suite mutates `source_erp`
    directly to prove incremental behavior — here via the same S3 API
    the connector itself reads through.
    """
    endpoint = urlsplit(HOST_MINIO_ENDPOINT)
    fs = pafs.S3FileSystem(
        access_key=MINIO_ACCESS_KEY_ID,
        secret_key=MINIO_SECRET_ACCESS_KEY,
        endpoint_override=endpoint.netloc,
        scheme=endpoint.scheme,
        region="us-east-1",
    )
    table = pa.table({k: [row[k] for row in rows] for k in rows[0]})
    with fs.open_output_stream(f"{BUCKET}/{key}") as stream:
        pacsv.write_csv(table, stream)


def test_object_key_mode_syncs_real_rows_from_suppliers_csv(jdoe_token: str) -> None:
    connection_name = _unique_name("minio_conn")
    _register_minio_connection(jdoe_token, connection_name)

    source_name = _unique_name("obj_suppliers")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/object-sources", token=jdoe_token,
        body={
            "name": source_name,
            "connection_name": connection_name,
            "bucket": BUCKET,
            "object_key": "landing/suppliers.csv",
            "format": "csv",
        },
    )
    assert status == 200, registration
    assert registration["object_key"] == "landing/suppliers.csv", registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, result
    assert result["row_count"] > 0, result


def test_key_prefix_mode_lists_and_reads_the_same_connection(jdoe_token: str) -> None:
    connection_name = _unique_name("minio_conn_shared")
    _register_minio_connection(jdoe_token, connection_name)

    object_source = _unique_name("obj_suppliers_shared")
    status, _ = _request(
        "POST", f"{CONNECTIVITY}/object-sources", token=jdoe_token,
        body={
            "name": object_source,
            "connection_name": connection_name,
            "bucket": BUCKET,
            "object_key": "landing/suppliers.csv",
            "format": "csv",
        },
    )
    assert status == 200

    prefix_source = _unique_name("obj_landing_prefix")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/object-sources", token=jdoe_token,
        body={
            "name": prefix_source,
            "connection_name": connection_name,
            "bucket": BUCKET,
            "key_prefix": "landing/",
            "format": "csv",
        },
    )
    assert status == 200, registration
    assert registration["key_prefix"] == "landing/", registration
    assert registration["object_key"] is None, registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": prefix_source})
    assert status == 200, result
    assert result["row_count"] > 0, result


def test_bad_format_is_rejected(jdoe_token: str) -> None:
    connection_name = _unique_name("minio_conn_badfmt")
    _register_minio_connection(jdoe_token, connection_name)

    status, body = _request(
        "POST", f"{CONNECTIVITY}/object-sources", token=jdoe_token,
        body={
            "name": _unique_name("obj_bad_format"),
            "connection_name": connection_name,
            "bucket": BUCKET,
            "object_key": "landing/suppliers.csv",
            "format": "xml",
        },
    )
    assert status == 400, body


def test_both_object_key_and_key_prefix_is_rejected(jdoe_token: str) -> None:
    connection_name = _unique_name("minio_conn_both")
    _register_minio_connection(jdoe_token, connection_name)

    status, body = _request(
        "POST", f"{CONNECTIVITY}/object-sources", token=jdoe_token,
        body={
            "name": _unique_name("obj_both"),
            "connection_name": connection_name,
            "bucket": BUCKET,
            "object_key": "landing/suppliers.csv",
            "key_prefix": "landing/",
            "format": "csv",
        },
    )
    assert status == 400, body


def test_incremental_with_object_key_is_rejected(jdoe_token: str) -> None:
    connection_name = _unique_name("minio_conn_incr_bad")
    _register_minio_connection(jdoe_token, connection_name)

    status, body = _request(
        "POST", f"{CONNECTIVITY}/object-sources", token=jdoe_token,
        body={
            "name": _unique_name("obj_incr_bad"),
            "connection_name": connection_name,
            "bucket": BUCKET,
            "object_key": "landing/suppliers.csv",
            "format": "csv",
            "incremental": True,
        },
    )
    assert status == 400, body


def test_registering_under_a_plugin_claimed_dataset_is_rejected(jdoe_token: str) -> None:
    connection_name = _unique_name("minio_conn_conflict")
    _register_minio_connection(jdoe_token, connection_name)

    status, body = _request(
        "POST", f"{CONNECTIVITY}/object-sources", token=jdoe_token,
        body={
            "name": "customers",
            "connection_name": connection_name,
            "bucket": BUCKET,
            "object_key": "landing/suppliers.csv",
            "format": "csv",
        },
    )
    assert status == 409, body
    assert "already claimed" in body["detail"], body


def test_incremental_prefix_only_fetches_new_objects(jdoe_token: str) -> None:
    connection_name = _unique_name("minio_conn_incr")
    _register_minio_connection(jdoe_token, connection_name)

    prefix = f"test-incremental/{_unique_name('run')}/"
    _write_csv_object(f"{prefix}a-first.csv", [{"id": 1, "name": "first"}])

    source_name = _unique_name("obj_incremental")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/object-sources", token=jdoe_token,
        body={
            "name": source_name,
            "connection_name": connection_name,
            "bucket": BUCKET,
            "key_prefix": prefix,
            "format": "csv",
            "incremental": True,
        },
    )
    assert status == 200, registration

    status, first = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, first
    assert first["row_count"] == 1, first

    _write_csv_object(f"{prefix}b-second.csv", [{"id": 2, "name": "second"}, {"id": 3, "name": "third"}])

    status, second = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, second
    # append mode: iceberg row_count reflects the table's new total, not
    # just the delta — same semantics proven in the SQL incremental test.
    assert second["row_count"] == first["row_count"] + 2, second

    status, sources = _request("GET", f"{CONNECTIVITY}/object-sources", token=jdoe_token)
    assert status == 200
    registered = next(s for s in sources if s["name"] == source_name)
    assert registered["last_synced_key"] == f"{prefix}b-second.csv", registered


def test_disable_then_sync_is_conflict_then_enable_restores_it(jdoe_token: str) -> None:
    connection_name = _unique_name("minio_conn_toggle")
    _register_minio_connection(jdoe_token, connection_name)

    source_name = _unique_name("obj_toggle")
    status, _ = _request(
        "POST", f"{CONNECTIVITY}/object-sources", token=jdoe_token,
        body={
            "name": source_name,
            "connection_name": connection_name,
            "bucket": BUCKET,
            "object_key": "landing/suppliers.csv",
            "format": "csv",
        },
    )
    assert status == 200

    status, body = _request("POST", f"{CONNECTIVITY}/object-sources/{source_name}/disable", token=jdoe_token)
    assert status == 200, body
    assert body["status"] == "disabled", body

    status, body = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 409, body

    status, body = _request("POST", f"{CONNECTIVITY}/object-sources/{source_name}/enable", token=jdoe_token)
    assert status == 200, body
    assert body["status"] == "active", body

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, result


def test_deleting_a_connection_still_in_use_is_409(jdoe_token: str) -> None:
    connection_name = _unique_name("minio_conn_delete")
    _register_minio_connection(jdoe_token, connection_name)

    source_name = _unique_name("obj_delete")
    status, _ = _request(
        "POST", f"{CONNECTIVITY}/object-sources", token=jdoe_token,
        body={
            "name": source_name,
            "connection_name": connection_name,
            "bucket": BUCKET,
            "object_key": "landing/suppliers.csv",
            "format": "csv",
        },
    )
    assert status == 200

    status, body = _request("DELETE", f"{CONNECTIVITY}/object-connections/{connection_name}", token=jdoe_token)
    assert status == 409, body
    assert source_name in body["detail"], body

    status, body = _request("DELETE", f"{CONNECTIVITY}/object-sources/{source_name}", token=jdoe_token)
    assert status == 200, body

    status, body = _request("DELETE", f"{CONNECTIVITY}/object-connections/{connection_name}", token=jdoe_token)
    assert status == 200, body
