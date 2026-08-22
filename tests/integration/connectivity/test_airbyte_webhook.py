"""Real end-to-end test of the Airbyte webhook -> catalog path (ADR 027).

No shared Airbyte instance exists yet to produce a real webhook call, so
this test writes a table directly into the shared Iceberg REST catalog
via pyiceberg — the same technique the 2026-08-20 technical spike used
to prove the destination-s3-data-lake connector writes into this exact
catalog — to stand in for "Airbyte already synced this." Everything
downstream of that point is real, no stubs: a real HTTP POST to
`/airbyte/webhook`, a real `_finalize_sync` call, real rows in
`sync_run`. This is not a mock of the webhook handler — it's a real
call with a substituted precondition.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import httpx
import pyarrow as pa
import pytest
from conftest import CONNECTIVITY, TENANT_ID, _unique_name
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError

CONNECTIVITY_DB_URL = f"postgresql://holon:{os.environ.get('POSTGRES_PASSWORD', 'holon12345')}@localhost:5432/holon_connectivity"

WEBHOOK_SECRET = os.environ.get("HOLON_AIRBYTE_WEBHOOK_SECRET")
if not WEBHOOK_SECRET:
    pytest.skip(
        "HOLON_AIRBYTE_WEBHOOK_SECRET not set for this stack — set it (see .env.example) to run "
        "the Airbyte webhook integration test; CI always sets it.",
        allow_module_level=True,
    )

AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "holon12345")


def _iceberg_catalog():
    return load_catalog(
        "test-airbyte-webhook",
        **{
            "type": "rest",
            "uri": "http://localhost:8181",
            "warehouse": "s3://holon-warehouse/",
            "s3.endpoint": "http://localhost:9000",
            "s3.access-key-id": "holon",
            "s3.secret-access-key": AWS_SECRET_ACCESS_KEY,
            "s3.region": "us-east-1",
            "s3.path-style-access": "true",
        },
    )


def _write_table_as_if_airbyte_had(namespace: str, table_name: str, rows: list[dict]) -> None:
    catalog = _iceberg_catalog()
    try:
        catalog.create_namespace(namespace)
    except NamespaceAlreadyExistsError:
        pass
    arrow_table = pa.Table.from_pylist(rows)
    table = catalog.create_table_if_not_exists((namespace, table_name), schema=arrow_table.schema)
    table.append(arrow_table)


async def _insert_airbyte_source_row(name: str, dataset_name: str, stream_name: str, connection_id: str) -> None:
    conn = await asyncpg.connect(CONNECTIVITY_DB_URL)
    try:
        await conn.execute(
            """
            INSERT INTO airbyte_source (
                tenant_id, name, dataset_name, stream_name, workspace_id, source_connector_type,
                airbyte_source_id, airbyte_destination_id, airbyte_connection_id, created_by_urn
            ) VALUES ($1, $2, $3, $4, 'main', 'postgres', $5, $6, $7, 'test')
            """,
            TENANT_ID, name, dataset_name, stream_name,
            f"src-{uuid.uuid4().hex}", f"dst-{uuid.uuid4().hex}", connection_id,
        )
    finally:
        await conn.close()


async def _fetch_sync_run(dataset_name: str) -> asyncpg.Record | None:
    conn = await asyncpg.connect(CONNECTIVITY_DB_URL)
    try:
        return await conn.fetchrow(
            "SELECT * FROM sync_run WHERE dataset_urn LIKE $1 ORDER BY id DESC LIMIT 1",
            f"%:dataset:{dataset_name}",
        )
    finally:
        await conn.close()


async def _fetch_airbyte_source(name: str) -> asyncpg.Record | None:
    conn = await asyncpg.connect(CONNECTIVITY_DB_URL)
    try:
        return await conn.fetchrow(
            "SELECT * FROM airbyte_source WHERE tenant_id = $1 AND name = $2", TENANT_ID, name
        )
    finally:
        await conn.close()


def test_successful_webhook_catalogues_the_dataset_via_finalize_sync() -> None:
    name = _unique_name("airbyte_webhook")
    dataset_name = _unique_name("airbyte_dataset")
    stream_name = "users"
    connection_id = f"conn-{uuid.uuid4().hex}"

    asyncio.run(_insert_airbyte_source_row(name, dataset_name, stream_name, connection_id))
    _write_table_as_if_airbyte_had(
        dataset_name, stream_name,
        [{"id": 1, "name": "Row One"}, {"id": 2, "name": "Row Two"}],
    )

    response = httpx.post(
        f"{CONNECTIVITY}/airbyte/webhook",
        params={"token": WEBHOOK_SECRET},
        json={"connectionId": connection_id, "status": "succeeded", "jobId": 999},
        timeout=30,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {"status": "recorded", "catalogued": True}, body

    sync_run = asyncio.run(_fetch_sync_run(dataset_name))
    assert sync_run is not None, "expected a sync_run row after a successful webhook"
    assert sync_run["row_count"] == 2, dict(sync_run)
    assert sync_run["iceberg_namespace"] == dataset_name, dict(sync_run)

    airbyte_source = asyncio.run(_fetch_airbyte_source(name))
    assert airbyte_source["last_sync_status"] == "succeeded", dict(airbyte_source)


def test_wrong_token_is_rejected() -> None:
    response = httpx.post(
        f"{CONNECTIVITY}/airbyte/webhook",
        params={"token": "definitely-not-the-secret"},
        json={"connectionId": "does-not-matter", "status": "succeeded"},
        timeout=30,
    )
    assert response.status_code == 403, response.text


def test_unknown_connection_id_is_ignored_not_errored() -> None:
    response = httpx.post(
        f"{CONNECTIVITY}/airbyte/webhook",
        params={"token": WEBHOOK_SECRET},
        json={"connectionId": f"conn-{uuid.uuid4().hex}", "status": "succeeded"},
        timeout=30,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ignored"}
