"""Catalog module — Dataset / DatasetVersion management.

Maintains the catalog by consuming `connectivity.sync.completed` off the
Platform Event Bus: the connector never blocks on cataloguing, and
cataloguing never blocks on the connector. Convergence is asynchronous
and independent.

Also owns sensitive column definitions: declaring sensitive source columns
where Dataset itself is owned makes ObjectType
classification a computed fact instead of a hardcoded one.
"""

from __future__ import annotations

import asyncio
import functools
import logging

import asyncpg

from holon_common import Classification, EventConsumer, most_restrictive

from . import lineage, ontology, resolver, search, serving_store

logger = logging.getLogger("knowledge.catalog")

DDL = """
CREATE TABLE IF NOT EXISTS dataset (
    urn TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dataset_version (
    urn TEXT PRIMARY KEY,
    dataset_urn TEXT NOT NULL REFERENCES dataset(urn),
    tenant_id TEXT NOT NULL,
    iceberg_namespace TEXT NOT NULL,
    iceberg_table TEXT NOT NULL,
    snapshot_id BIGINT NOT NULL,
    row_count INTEGER NOT NULL,
    location TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


async def list_datasets(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT d.urn, d.display_name, v.urn AS latest_version_urn, v.snapshot_id,
               v.row_count, v.location, v.created_at
        FROM dataset d
        JOIN LATERAL (
            SELECT * FROM dataset_version dv
            WHERE dv.dataset_urn = d.urn ORDER BY dv.created_at DESC LIMIT 1
        ) v ON true
        WHERE d.tenant_id = $1
        ORDER BY d.urn
        """,
        tenant_id,
    )
    return [dict(row) for row in rows]


async def get_dataset_version_by_urn(pool: asyncpg.Pool, dataset_version_urn: str) -> dict | None:
    """Fetch a specific dataset version by URN."""
    row = await pool.fetchrow("SELECT * FROM dataset_version WHERE urn = $1", dataset_version_urn)
    return dict(row) if row else None


async def latest_dataset_version_urn(pool: asyncpg.Pool, dataset_urn: str) -> str | None:
    """Fetch the URN of the latest version for a dataset."""
    row = await pool.fetchrow(
        "SELECT urn FROM dataset_version WHERE dataset_urn = $1 ORDER BY created_at DESC LIMIT 1",
        dataset_urn,
    )
    return row["urn"] if row else None


async def _catalogue_sync(conn: asyncpg.Connection, tenant_id: str, workspace_id: str, payload: dict) -> None:
    dataset_name = payload["dataset_name"]

    await conn.execute(
        "INSERT INTO dataset (urn, tenant_id, display_name) VALUES ($1, $2, $3) "
        "ON CONFLICT (urn) DO NOTHING",
        payload["dataset_urn"],
        tenant_id,
        dataset_name,
    )
    await conn.execute(
        """
        INSERT INTO dataset_version (
            urn, dataset_urn, tenant_id, iceberg_namespace, iceberg_table,
            snapshot_id, row_count, location
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (urn) DO NOTHING
        """,
        payload["dataset_version_urn"],
        payload["dataset_urn"],
        tenant_id,
        payload["iceberg_namespace"],
        payload["iceberg_table"],
        payload["snapshot_id"],
        payload["row_count"],
        payload["location"],
    )

    source_dataset_version_urn = payload.get("source_dataset_version_urn")
    if source_dataset_version_urn:
        await lineage.record_edge(
            conn, tenant_id, source_dataset_version_urn, payload["dataset_version_urn"], "derived_from"
        )

    dynamic_type = await ontology.get_object_type_by_dataset(conn, tenant_id, payload["dataset_urn"])
    if dynamic_type is None:
        logger.info("dataset %r synced with no ObjectType mapping — catalogued, not yet an ObjectType", dataset_name)
        return
    object_type_urn = dynamic_type["urn"]
    property_mapping = dynamic_type["property_mapping"]
    column_classification = {
        col: Classification(value)
        for col, value in (dynamic_type.get("column_classification") or {}).items()
    }

    await lineage.record_edge(conn, tenant_id, payload["dataset_version_urn"], object_type_urn, "maps_to")

    effective_classification = {
        source_col: column_classification.get(source_col, Classification.INTERNAL)
        for source_col in property_mapping.values()
    }

    for prop_name, source_col in property_mapping.items():
        col_cls = effective_classification[source_col]
        await lineage.record_edge(
            conn, tenant_id, payload["dataset_version_urn"], object_type_urn, "maps_to",
            source_column=source_col, target_property=prop_name,
        )
        await ontology.upsert_property_classification(conn, object_type_urn, source_col, col_cls.value)

    overall_classification = (
        most_restrictive(*effective_classification.values()) if effective_classification else Classification.INTERNAL
    )
    await conn.execute(
        "UPDATE object_type SET classification = $1 WHERE urn = $2",
        overall_classification.value,
        object_type_urn,
    )


async def _materialize_sync(
    pool: asyncpg.Pool,
    tenant_id: str,
    workspace_id: str,
    payload: dict,
    iceberg_config: dict,
    opensearch_url: str,
    opensearch_password: str,
    allowed_countries: set[str],
) -> None:
    """Materialize dataset rows to serving store and index into OpenSearch."""
    dataset_name = payload["dataset_name"]
    dynamic_type = await ontology.get_object_type_by_dataset(pool, tenant_id, payload["dataset_urn"])
    if dynamic_type is None:
        return
    object_type_name = dynamic_type["name"]
    object_type_urn = dynamic_type["urn"]
    property_mapping = dynamic_type["property_mapping"]
    fetch_all = functools.partial(resolver.fetch_generic, dataset_name)

    rows = await asyncio.to_thread(fetch_all, **iceberg_config)
    async with pool.acquire() as conn, conn.transaction():
        await serving_store.materialize(
            conn,
            object_type=object_type_name,
            tenant_id=tenant_id,
            snapshot_id=payload["snapshot_id"],
            rows=rows,
        )

    object_type = await ontology.get_object_type(pool, object_type_urn)
    property_types = (object_type or {}).get("property_types") or {}
        # Values that fail Value Type validation fail to index.
    # Holon still materializes them (Object Explorer can show bad data for
    # repair) but OpenSearch only receives the valid partition.
    index_rows = rows
    if property_types:
        index_rows, _invalid = await ontology.partition_rows_by_property_types(
            pool,
            tenant_id,
            property_mapping=property_mapping,
            property_types=property_types,
            rows=rows,
        )
        if _invalid:
            logger.warning(
                "object type %s: skipping %d/%d rows from search index (Value Type validation failed)",
                object_type_name,
                len(_invalid),
                len(rows),
            )

    shared_rows = await ontology.list_shared_property_types(pool, tenant_id)
    shared_by_name = {row["api_name"]: row for row in shared_rows}
    await search.index_rows(
        opensearch_url,
        opensearch_password,
        object_type_name=object_type_name,
        tenant_id=tenant_id,
        classification=object_type["classification"],
        property_mapping=property_mapping,
        rows=index_rows,
        allowed_countries=allowed_countries,
        property_types=property_types,
        shared_property_types=shared_by_name,
    )


async def consume_events(
    pool: asyncpg.Pool,
    consumer: EventConsumer,
    workspace_id: str,
    iceberg_config: dict,
    opensearch_url: str,
    opensearch_password: str,
    allowed_countries: set[str],
) -> None:
    """Consume sync events and catalogue/materialize datasets."""
    await consumer.start()
    async for event in consumer:
        try:
            if event.event_type == "connectivity.sync.completed":
                async with pool.acquire() as conn, conn.transaction():
                    await _catalogue_sync(conn, event.tenant_id, workspace_id, event.payload)
                await _materialize_sync(
                    pool,
                    event.tenant_id,
                    workspace_id,
                    event.payload,
                    iceberg_config,
                    opensearch_url,
                    opensearch_password,
                    allowed_countries,
                )
                logger.info("catalogued %s", event.payload["dataset_version_urn"])
        except Exception:
            logger.exception("failed to catalogue event %s, skipping", event.event_id)
        await consumer.commit()  # Idempotent upserts make redelivery safe


_INDEX_METADATA_KEYS = frozenset({"materializedAt", "sourceLagSeconds", "degraded", "_maskedFields", "asOf"})


async def reindex_object_type_search(
    pool: asyncpg.Pool,
    *,
    object_type_name: str,
    object_type_urn: str,
    tenant_id: str,
    opensearch_url: str,
    opensearch_password: str,
    allowed_countries: set[str],
) -> dict:
    """Rebuild OpenSearch documents for one ObjectType from the serving store.

    Foundry exposes a similar "Reindex datasources" action when render
    hints or mappings change — Holon re-reads materialized rows and
    re-applies hint-driven indexing rules.
    """
    object_type = await ontology.get_object_type(pool, object_type_urn)
    if object_type is None:
        raise ValueError(f"unknown ObjectType: {object_type_name!r}")

    property_mapping = object_type["property_mapping"]
    property_types = object_type.get("property_types") or {}
    materialized = await serving_store.list_instances(pool, object_type_name, tenant_id)
    rows = [{k: v for k, v in row.items() if k not in _INDEX_METADATA_KEYS} for row in materialized]

    skipped = 0
    index_rows = rows
    if property_types:
        index_rows, invalid = await ontology.partition_rows_by_property_types(
            pool,
            tenant_id,
            property_mapping=property_mapping,
            property_types=property_types,
            rows=rows,
        )
        skipped = len(invalid)

    await search.delete_object_type_documents(
        opensearch_url,
        opensearch_password,
        object_type_name=object_type_name,
        tenant_id=tenant_id,
    )

    shared_rows = await ontology.list_shared_property_types(pool, tenant_id)
    shared_by_name = {row["api_name"]: row for row in shared_rows}
    if index_rows:
        await search.index_rows(
            opensearch_url,
            opensearch_password,
            object_type_name=object_type_name,
            tenant_id=tenant_id,
            classification=object_type["classification"],
            property_mapping=property_mapping,
            rows=index_rows,
            allowed_countries=allowed_countries,
            property_types=property_types,
            shared_property_types=shared_by_name,
        )

    return {
        "object_type": object_type_name,
        "indexed": len(index_rows),
        "skipped_invalid": skipped,
        "materialized_total": len(rows),
    }
