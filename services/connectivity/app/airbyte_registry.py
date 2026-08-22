"""Airbyte-backed source registry (ADR 027).

Maps a Holon dataset name to the Airbyte source/destination/connection
triad that actually produces it. Mirrors `generic_source_registry.py`'s
shape (Postgres-backed CRUD, a conflict error, public columns that never
leak a secret) for the same reason that registry has it: one dataset
name is one claim on the same `dataset` namespace `run_sync` dispatches
over.

Deliberately does **not** persist the source connector's configuration
(DB passwords, API keys, ...) at rest — it is sent to Airbyte once, at
creation time, and then only Airbyte holds it. Storing a second copy
here would recreate exactly the dual secret-custody ADR 027 rules out.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

from .airbyte_client import AirbyteClient

DDL = """
CREATE TABLE IF NOT EXISTS airbyte_source (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    dataset_name TEXT NOT NULL,
    -- The Airbyte source's own stream name, e.g. "users" — not the same
    -- as dataset_name in general. The destination's Iceberg table is
    -- (namespace=dataset_name, table=stream_name); a 2026-08-20 live test
    -- against a real source-faker -> destination-s3-data-lake sync found
    -- this the hard way (assuming table_name == dataset_name 404s against
    -- a real multi-word stream name like "users").
    stream_name TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    source_connector_type TEXT NOT NULL,
    airbyte_source_id TEXT NOT NULL,
    airbyte_destination_id TEXT NOT NULL,
    airbyte_connection_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    last_sync_status TEXT,
    last_synced_at TIMESTAMPTZ,
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

-- Airbyte connection ids are globally unique on the Airbyte side — the
-- webhook resolves a job's connection_id back to a row with no tenant_id
-- in hand yet, so this needs its own unique lookup, not just the
-- (tenant_id, name) primary key.
CREATE UNIQUE INDEX IF NOT EXISTS airbyte_source_connection_id_idx
    ON airbyte_source (airbyte_connection_id);
"""

_PUBLIC_COLUMNS = (
    "tenant_id, name, dataset_name, stream_name, workspace_id, source_connector_type, "
    "airbyte_connection_id, status, last_sync_status, last_synced_at, "
    "created_by_urn, created_at"
)


class AirbyteSourceConflictError(ValueError):
    pass


class AirbyteSourceConfigError(ValueError):
    pass


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


def _s3_bucket_name(warehouse: str) -> str:
    return warehouse.removeprefix("s3://").removeprefix("s3a://").rstrip("/").split("/")[0]


def build_iceberg_destination_configuration(
    dataset_name: str,
    *,
    catalog_uri: str,
    warehouse: str,
    s3_endpoint: str,
    access_key: str,
    secret_key: str,
    region: str,
) -> dict[str, Any]:
    """The *only* destination configuration this wrapper ever sends to
    Airbyte — always the `destination-s3-data-lake` connector (ADR 027;
    not the legacy `destination-iceberg`, which has an unresolved MinIO
    bug — confirmed by the 2026-08-20 technical spike), always the same
    shared Iceberg REST catalog every other dataset already lands in.

    Deliberately never admin-supplied: a caller choosing an arbitrary
    destination would defeat the ADR's "one governed integration point"
    design outright — every Airbyte-backed source in Holon lands in the
    same catalog Knowledge already reads, or it isn't a Holon dataset.
    Shape matches the config verified end-to-end against a real
    source-faker -> destination-s3-data-lake sync into this catalog.
    """
    return {
        "access_key_id": access_key,
        "secret_access_key": secret_key,
        "s3_bucket_name": _s3_bucket_name(warehouse),
        "s3_bucket_region": region,
        "s3_endpoint": s3_endpoint,
        "warehouse_location": warehouse,
        "main_branch_name": "main",
        "catalog_type": {
            "catalog_type": "REST",
            "server_uri": catalog_uri,
            "namespace": dataset_name,
        },
    }


async def register_airbyte_source(
    pool: asyncpg.Pool,
    *,
    client: AirbyteClient,
    airbyte_workspace_id: str,
    tenant_id: str,
    name: str,
    dataset_name: str,
    stream_name: str,
    workspace_id: str,
    source_connector_type: str,
    source_configuration: dict[str, Any],
    iceberg_config: dict[str, Any],
    created_by_urn: str,
    reserved_dataset_names: frozenset[str] = frozenset(),
) -> dict:
    """Creates the source+destination+connection triad in Airbyte, then
    persists the mapping. If the local row already exists this raises
    before ever calling Airbyte — no orphaned Airbyte objects from a
    name collision caught after the fact.

    `source_configuration` is the only connector config a caller ever
    supplies — the destination is always
    `build_iceberg_destination_configuration(...)`, never theirs to pick.
    """
    if await get_source(pool, tenant_id, name) is not None:
        raise AirbyteSourceConflictError(f"an Airbyte source is already registered as {name!r}")
    if dataset_name in reserved_dataset_names:
        raise AirbyteSourceConfigError(f"dataset {dataset_name!r} is reserved")

    airbyte_source_id = await client.create_source(
        name=f"holon-{tenant_id}-{name}", workspace_id=airbyte_workspace_id, configuration=source_configuration
    )
    destination_configuration = build_iceberg_destination_configuration(dataset_name, **iceberg_config)
    airbyte_destination_id = await client.create_destination(
        name=f"holon-{tenant_id}-{name}", workspace_id=airbyte_workspace_id, configuration=destination_configuration
    )
    airbyte_connection_id = await client.create_connection(
        name=f"holon-{tenant_id}-{name}",
        source_id=airbyte_source_id,
        destination_id=airbyte_destination_id,
        namespace=dataset_name,
    )

    await pool.execute(
        """
        INSERT INTO airbyte_source (
            tenant_id, name, dataset_name, stream_name, workspace_id, source_connector_type,
            airbyte_source_id, airbyte_destination_id, airbyte_connection_id, created_by_urn
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        tenant_id, name, dataset_name, stream_name, workspace_id, source_connector_type,
        airbyte_source_id, airbyte_destination_id, airbyte_connection_id, created_by_urn,
    )
    return await get_source(pool, tenant_id, name)


async def get_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> dict | None:
    row = await pool.fetchrow(
        f"SELECT {_PUBLIC_COLUMNS} FROM airbyte_source WHERE tenant_id = $1 AND name = $2", tenant_id, name
    )
    return None if row is None else dict(row)


async def list_sources(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        f"SELECT {_PUBLIC_COLUMNS} FROM airbyte_source WHERE tenant_id = $1 ORDER BY name", tenant_id
    )
    return [dict(row) for row in rows]


async def get_ids_by_name(pool: asyncpg.Pool, tenant_id: str, name: str) -> dict | None:
    """Internal ids `main.py`'s sync-trigger/delete routes need — kept
    out of `_PUBLIC_COLUMNS` on principle even though these ids aren't
    secrets, to keep exactly one query shape that's safe to hand back to
    a caller unmodified.
    """
    row = await pool.fetchrow(
        "SELECT airbyte_source_id, airbyte_destination_id, airbyte_connection_id "
        "FROM airbyte_source WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )
    return None if row is None else dict(row)


async def get_by_connection_id(pool: asyncpg.Pool, airbyte_connection_id: str) -> dict | None:
    row = await pool.fetchrow(
        "SELECT tenant_id, name, dataset_name, stream_name, workspace_id "
        "FROM airbyte_source WHERE airbyte_connection_id = $1",
        airbyte_connection_id,
    )
    return None if row is None else dict(row)


async def mark_sync_result(pool: asyncpg.Pool, *, tenant_id: str, name: str, status: str, synced_at: datetime) -> None:
    await pool.execute(
        "UPDATE airbyte_source SET last_sync_status = $1, last_synced_at = $2 WHERE tenant_id = $3 AND name = $4",
        status, synced_at, tenant_id, name,
    )


async def delete_source(pool: asyncpg.Pool, *, client: AirbyteClient, tenant_id: str, name: str) -> None:
    ids = await get_ids_by_name(pool, tenant_id, name)
    if ids is None:
        return
    await client.delete_connection(ids["airbyte_connection_id"])
    await client.delete_source(ids["airbyte_source_id"])
    await client.delete_destination(ids["airbyte_destination_id"])
    await pool.execute("DELETE FROM airbyte_source WHERE tenant_id = $1 AND name = $2", tenant_id, name)
