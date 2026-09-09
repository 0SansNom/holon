"""Catalog module — Dataset and DatasetVersion management and classification propagation."""

from __future__ import annotations

import asyncio
import logging
import time

import asyncpg

from holon_common import Classification, EventConsumer, most_restrictive

from . import lineage, link_overlays, ontology, relation_links, resolver, search, serving_store

logger = logging.getLogger("knowledge.catalog")


class TransientCatalogError(Exception):
    """Retryable ingest failure — do not commit the Kafka offset."""


join_link_backfill_status = "idle"
_ENSURE_MISS_TTL_SECONDS = 30.0
_ensure_locks: dict[str, asyncio.Lock] = {}
_ensure_miss_until: dict[str, float] = {}

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


async def list_dataset_versions(pool: asyncpg.Pool, tenant_id: str, dataset_urn: str) -> list[dict]:
    """Full snapshot history for a dataset — every sync/pipeline-run
    that ever produced one, newest first. `_catalogue_sync` already
    inserts one immutable row per snapshot; this was always here, just
    never queried back out through an endpoint.
    """
    rows = await pool.fetch(
        "SELECT * FROM dataset_version WHERE tenant_id = $1 AND dataset_urn = $2 ORDER BY created_at DESC",
        tenant_id, dataset_urn,
    )
    return [dict(row) for row in rows]


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
    try:
        rows = await _fetch_dataset_rows(dataset_name, tenant_id, iceberg_config)
    except Exception as exc:
        if _is_transient(exc):
            raise TransientCatalogError(str(exc)) from exc
        raise
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
    instance_markings = await ontology.get_instance_markings_bulk(
        pool,
        object_type_urn=object_type_urn,
        tenant_id=tenant_id,
        instance_ids=[str(row["id"]) for row in index_rows],
    )
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
        instance_markings=instance_markings,
    )


_JOIN_LOAD_RETRIES = 4
_JOIN_LOAD_RETRY_DELAY_SECONDS = 1.5
_EVENT_ATTEMPTS = 8
_EVENT_RETRY_DELAY_SECONDS = 2.0


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, TransientCatalogError):
        return True
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError)):
        return True
    name = type(exc).__name__
    return any(
        token in name
        for token in ("Timeout", "Connection", "Connect", "HTTPError", "HTTPStatus", "NoSuchTable")
    )


async def _latest_snapshot_id(pool: asyncpg.Pool, dataset_urn: str) -> int:
    row = await pool.fetchrow(
        "SELECT snapshot_id FROM dataset_version WHERE dataset_urn = $1 ORDER BY created_at DESC LIMIT 1",
        dataset_urn,
    )
    if row is None or row["snapshot_id"] is None:
        return 0
    return int(row["snapshot_id"])


async def _fetch_dataset_rows(
    dataset_name: str,
    tenant_id: str,
    iceberg_config: dict,
    *,
    retries: int = _JOIN_LOAD_RETRIES,
) -> list[dict]:
    """Load an Iceberg table, retrying catalog lag after `sync.completed`."""
    from pyiceberg.exceptions import NoSuchTableError

    attempts = max(1, retries)
    last_missing: NoSuchTableError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await asyncio.to_thread(
                resolver.fetch_generic,
                dataset_name,
                **{**iceberg_config, "tenant_id": tenant_id},
            )
        except NoSuchTableError as exc:
            last_missing = exc
            if attempt == attempts:
                raise
            await asyncio.sleep(_JOIN_LOAD_RETRY_DELAY_SECONDS)
        except Exception as exc:
            if not _is_transient(exc) or attempt == attempts:
                raise
            await asyncio.sleep(_JOIN_LOAD_RETRY_DELAY_SECONDS)
    raise last_missing if last_missing is not None else NoSuchTableError(dataset_name)


async def _replace_join_pairs_for_relations(
    pool: asyncpg.Pool,
    tenant_id: str,
    relations: list[dict],
    rows: list[dict],
    snapshot_id: int,
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        for relation in relations:
            src_col = relation.get("join_source_column")
            tgt_col = relation.get("join_target_column")
            if not src_col or not tgt_col:
                continue
            pairs = relation_links.pairs_from_join_rows(rows, src_col, tgt_col)
            await relation_links.replace_pairs(
                conn,
                tenant_id=tenant_id,
                relation_urn=relation["urn"],
                snapshot_id=snapshot_id,
                pairs=pairs,
            )
            overlays = await link_overlays.list_overlays(
                conn, tenant_id=tenant_id, relation_urn=relation["urn"]
            )
            absorbed = link_overlays.overlays_absorbed_by_pairs(overlays, set(pairs))
            await link_overlays.delete_overlays(
                conn,
                tenant_id=tenant_id,
                relation_urn=relation["urn"],
                pairs=absorbed,
            )


async def _materialize_join_links(
    pool: asyncpg.Pool,
    tenant_id: str,
    payload: dict,
    iceberg_config: dict,
) -> None:
    """Project join_dataset Iceberg rows into `relation_link` for graph reads.

    Runs even when the dataset has no ObjectType (bridge tables usually don't).
    Missing Iceberg tables skip the replace so existing pairs stay put.
    """
    from pyiceberg.exceptions import NoSuchTableError

    relations = await ontology.list_relation_types_for_join_dataset(
        pool, tenant_id, payload["dataset_urn"]
    )
    if not relations:
        return
    snapshot_id = payload["snapshot_id"]
    stale: list[dict] = []
    for relation in relations:
        synced = await relation_links.get_sync_snapshot(
            pool, tenant_id=tenant_id, relation_urn=relation["urn"]
        )
        if synced == snapshot_id:
            continue
        stale.append(relation)
    if not stale:
        return
    dataset_name = payload["dataset_name"]
    try:
        rows = await _fetch_dataset_rows(dataset_name, tenant_id, iceberg_config)
    except NoSuchTableError as exc:
        raise TransientCatalogError(
            f"join dataset {dataset_name!r} missing in Iceberg after sync"
        ) from exc
    await _replace_join_pairs_for_relations(pool, tenant_id, stale, rows, snapshot_id)


async def refresh_join_links_for_relation(
    pool: asyncpg.Pool,
    relation: dict,
    iceberg_config: dict,
    *,
    snapshot_id: int | None = None,
) -> None:
    """Materialize one join_dataset RelationType from Iceberg.

    NoSuchTableError skips the replace so existing pairs stay put.
    """
    from pyiceberg.exceptions import NoSuchTableError

    if (relation.get("storage_kind") or "") != "join_dataset":
        return
    join_urn = relation.get("join_dataset_urn")
    tenant_id = relation.get("tenant_id")
    if not join_urn or not tenant_id:
        return
    dataset_name = join_urn.rsplit(":", 1)[-1]
    if snapshot_id is None:
        snapshot_id = await _latest_snapshot_id(pool, join_urn)
    synced = await relation_links.get_sync_snapshot(
        pool, tenant_id=tenant_id, relation_urn=relation["urn"]
    )
    if synced == snapshot_id:
        return
    try:
        rows = await _fetch_dataset_rows(
            dataset_name, tenant_id, iceberg_config, retries=1
        )
    except NoSuchTableError:
        logger.warning(
            "join dataset %r missing in Iceberg; skipping relation_link refresh for %s",
            dataset_name,
            relation.get("urn"),
        )
        return
    await _replace_join_pairs_for_relations(
        pool, tenant_id, [relation], rows, snapshot_id
    )


async def ensure_join_links_materialized(
    pool: asyncpg.Pool, relation: dict, iceberg_config: dict
) -> None:
    """One-shot graph-path fill when this RelationType has never been projected."""
    if (relation.get("storage_kind") or "") != "join_dataset":
        return
    urn = relation.get("urn")
    tenant_id = relation.get("tenant_id")
    if not urn or not tenant_id:
        return
    if await relation_links.get_sync_snapshot(pool, tenant_id=tenant_id, relation_urn=urn) is not None:
        return
    now = time.monotonic()
    if _ensure_miss_until.get(urn, 0.0) > now:
        return
    lock = _ensure_locks.setdefault(urn, asyncio.Lock())
    async with lock:
        if await relation_links.get_sync_snapshot(pool, tenant_id=tenant_id, relation_urn=urn) is not None:
            return
        await refresh_join_links_for_relation(pool, relation, iceberg_config)
        if await relation_links.get_sync_snapshot(pool, tenant_id=tenant_id, relation_urn=urn) is None:
            _ensure_miss_until[urn] = time.monotonic() + _ENSURE_MISS_TTL_SECONDS


async def sync_join_links_after_relation_change(
    pool: asyncpg.Pool,
    relation: dict,
    iceberg_config: dict,
    *,
    previous: dict | None = None,
) -> None:
    """Refresh or drop `relation_link` rows when a RelationType's join storage changes."""
    prev_kind = (previous or {}).get("storage_kind") or ""
    new_kind = relation.get("storage_kind") or ""
    if prev_kind == "join_dataset" and new_kind != "join_dataset":
        async with pool.acquire() as conn, conn.transaction():
            await relation_links.delete_pairs(
                conn, tenant_id=relation["tenant_id"], relation_urn=relation["urn"]
            )
        return
    if new_kind != "join_dataset":
        return
    if previous is not None and prev_kind == "join_dataset" and (
        previous.get("join_dataset_urn") == relation.get("join_dataset_urn")
        and previous.get("join_source_column") == relation.get("join_source_column")
        and previous.get("join_target_column") == relation.get("join_target_column")
    ):
        return
    await refresh_join_links_for_relation(pool, relation, iceberg_config)


async def backfill_join_links(pool: asyncpg.Pool, iceberg_config: dict) -> None:
    """Fill `relation_link` for every join_dataset RelationType (post-migrate cutover)."""
    global join_link_backfill_status
    join_link_backfill_status = "running"
    try:
        relations = await ontology.list_join_dataset_relation_types(pool)
        for relation in relations:
            try:
                await refresh_join_links_for_relation(pool, relation, iceberg_config)
            except Exception:
                logger.exception("join_link backfill failed for %s", relation.get("urn"))
        join_link_backfill_status = "ok"
    except Exception:
        join_link_backfill_status = "error"
        logger.exception("join_link backfill aborted")


async def _process_sync_completed(
    pool: asyncpg.Pool,
    event,
    workspace_id: str,
    iceberg_config: dict,
    opensearch_url: str,
    opensearch_password: str,
    allowed_countries: set[str],
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await _catalogue_sync(conn, event.tenant_id, workspace_id, event.payload)
    dynamic_type = await ontology.get_object_type_by_dataset(
        pool, event.tenant_id, event.payload["dataset_urn"]
    )
    if dynamic_type is not None:
        from .ontology import definition_cache

        definition_cache.invalidate_object_type(
            urn=dynamic_type["urn"], tenant_id=event.tenant_id
        )
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
    await _materialize_join_links(
        pool,
        event.tenant_id,
        event.payload,
        iceberg_config,
    )
    logger.info("catalogued %s", event.payload["dataset_version_urn"])


async def consume_events(
    pool: asyncpg.Pool,
    consumer: EventConsumer,
    workspace_id: str,
    iceberg_config: dict,
    opensearch_url: str,
    opensearch_password: str,
    allowed_countries: set[str],
) -> None:
    """Consume sync events. Transient failures retry; poison goes to the DLQ then commit."""
    await consumer.start()
    async for event in consumer:
        if event.event_type != "connectivity.sync.completed":
            await consumer.commit()
            continue
        attempts = 0
        while True:
            try:
                await _process_sync_completed(
                    pool,
                    event,
                    workspace_id,
                    iceberg_config,
                    opensearch_url,
                    opensearch_password,
                    allowed_countries,
                )
                await consumer.commit()
                break
            except Exception as exc:
                attempts += 1
                if _is_transient(exc) and attempts < _EVENT_ATTEMPTS:
                    logger.warning(
                        "transient catalogue failure for %s (attempt %s/%s): %s",
                        event.event_id,
                        attempts,
                        _EVENT_ATTEMPTS,
                        exc,
                    )
                    await asyncio.sleep(_EVENT_RETRY_DELAY_SECONDS)
                    continue
                logger.exception("failed to catalogue event %s", event.event_id)
                if await consumer.quarantine_envelope(event, exc):
                    await consumer.commit()
                    break
                await asyncio.sleep(_EVENT_RETRY_DELAY_SECONDS)


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
    instance_markings = await ontology.get_instance_markings_bulk(
        pool,
        object_type_urn=object_type_urn,
        tenant_id=tenant_id,
        instance_ids=[str(row["id"]) for row in index_rows],
    )
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
            instance_markings=instance_markings,
        )

    return {
        "object_type": object_type_name,
        "indexed": len(index_rows),
        "skipped_invalid": skipped,
        "materialized_total": len(rows),
    }
