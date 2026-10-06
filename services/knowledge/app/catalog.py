"""Catalog module — Dataset and DatasetVersion management and classification propagation."""

from __future__ import annotations

import asyncio
import json
import logging
import time

import asyncpg

from holon_common import Classification, EventConsumer, most_restrictive

from . import lineage, link_overlays, ontology, relation_links, resolver, search, serving_store
from .search_metrics import (
    SEARCH_DOCUMENTS_OUTSIDE_POLICY,
    SEARCH_REINDEX_FAILED_OBJECT_TYPES,
    SEARCH_ROWS_SKIPPED_INVALID,
)

logger = logging.getLogger("knowledge.catalog")


class TransientCatalogError(Exception):
    """Retryable ingest failure — do not commit the Kafka offset."""


join_link_backfill_status = "idle"
search_reindex_status: dict = {"state": "idle"}
search_skipped_invalid: dict[str, int] = {}
_SEARCH_REINDEX_RETRY_SECONDS = (30.0, 60.0, 120.0, 300.0)
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


def _producer_json(payload: dict) -> str | None:
    producer = payload.get("producer")
    if not isinstance(producer, dict) or not producer:
        connector_urn = payload.get("connector_urn")
        if connector_urn:
            producer = {"kind": "connector", "connector_urn": connector_urn}
        else:
            return None
    return json.dumps(producer)


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
            snapshot_id, row_count, location, producer
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
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
        _producer_json(payload),
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
    _record_skipped_invalid(tenant_id, object_type_urn, object_type_name, len(rows) - len(index_rows))

    shared_rows = await ontology.list_shared_property_types(pool, tenant_id)
    shared_by_name = {row["api_name"]: row for row in shared_rows}
    instance_markings = await ontology.get_instance_markings_bulk(
        pool,
        object_type_urn=object_type_urn,
        tenant_id=tenant_id,
        instance_ids=[str(row["id"]) for row in index_rows],
    )
    classifications = await ontology.get_property_classifications(pool, object_type_urn)
    await search.index_rows(
        opensearch_url,
        opensearch_password,
        object_type_name=object_type_name,
        tenant_id=tenant_id,
        classification=object_type["classification"],
        property_mapping=property_mapping,
        rows=index_rows,
        property_types=property_types,
        shared_property_types=shared_by_name,
        property_classifications=classifications,
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
    purge_missing: bool = True,
) -> dict:
    """Rebuild OpenSearch documents for one ObjectType from the serving store.

    Holon exposes a "Reindex datasources" action when render
    hints or mappings change — Holon re-reads materialized rows and
    re-applies hint-driven indexing rules.

    Documents are written before any delete, so a crash does not empty
    the type. ``purge_missing`` then drops rows that were not rewritten
    and that predate this call. The startup migration passes False: it
    only overwrites, and leaves removal to an explicit reindex.
    """
    started = time.time()
    generation = time.time_ns()
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
    _record_skipped_invalid(tenant_id, object_type_urn, object_type_name, skipped)

    shared_rows = await ontology.list_shared_property_types(pool, tenant_id)
    shared_by_name = {row["api_name"]: row for row in shared_rows}
    instance_markings = await ontology.get_instance_markings_bulk(
        pool,
        object_type_urn=object_type_urn,
        tenant_id=tenant_id,
        instance_ids=[str(row["id"]) for row in index_rows],
    )
    if index_rows:
        classifications = await ontology.get_property_classifications(pool, object_type_urn)
        await search.index_rows(
            opensearch_url,
            opensearch_password,
            object_type_name=object_type_name,
            tenant_id=tenant_id,
            classification=object_type["classification"],
            property_mapping=property_mapping,
            rows=index_rows,
            property_types=property_types,
            shared_property_types=shared_by_name,
            property_classifications=classifications,
            instance_markings=instance_markings,
            index_generation=generation,
            indexed_at=time.time(),
        )
    if purge_missing:
        await search.delete_object_type_documents(
            opensearch_url,
            opensearch_password,
            object_type_name=object_type_name,
            tenant_id=tenant_id,
            keep_generation=generation,
            indexed_before=started,
        )

    return {
        "object_type": object_type_name,
        "indexed": len(index_rows),
        "skipped_invalid": skipped,
        "materialized_total": len(rows),
    }


async def reindex_search_from_serving_store(
    pool: asyncpg.Pool,
    opensearch_url: str,
    opensearch_password: str,
    *,
    retry_seconds: tuple[float, ...] = _SEARCH_REINDEX_RETRY_SECONDS,
) -> int:
    """Rewrite search documents that are still on an older policy version.

    Already-current indexes are left alone. Rewrites overwrite by document
    id and do not delete first, so a crash cannot empty a type. Failures
    on one type do not stop the others; failed types are retried with
    backoff until they succeed. Progress is published in
    ``search_reindex_status`` for ``/ready``.
    """
    global search_reindex_status
    try:
        pending = await _count_outside_policy_or_none(opensearch_url, opensearch_password)
        if pending == 0:
            search_reindex_status = {"state": "ok", "pending_outside_policy": 0, "failed": []}
            logger.info("search index already at policy_version %s", search.POLICY_VERSION)
            return 0
        rows = await pool.fetch("SELECT urn, name, tenant_id FROM object_type ORDER BY tenant_id, name")
        remaining = list(rows)
        done = 0
        attempt = 0
        search_reindex_status = {"state": "running", "pending_outside_policy": pending, "total": len(rows), "failed": []}
        while True:
            failed = []
            for row in remaining:
                try:
                    await reindex_object_type_search(
                        pool,
                        object_type_name=row["name"],
                        object_type_urn=row["urn"],
                        tenant_id=row["tenant_id"],
                        opensearch_url=opensearch_url,
                        opensearch_password=opensearch_password,
                        purge_missing=False,
                    )
                    done += 1
                except ValueError:
                    logger.info("search policy reindex: %s no longer exists, skipping", row["urn"])
                except Exception:
                    logger.exception("search policy reindex failed for %s", row["urn"])
                    failed.append(row)
            if not failed:
                break
            delay = retry_seconds[min(attempt, len(retry_seconds) - 1)]
            attempt += 1
            search_reindex_status = {
                "state": "degraded", "pending_outside_policy": pending, "total": len(rows),
                "failed": [row["urn"] for row in failed], "attempts": attempt,
            }
            SEARCH_REINDEX_FAILED_OBJECT_TYPES.set(len(failed))
            logger.warning("search policy reindex: %d object types failed, retrying in %ss", len(failed), delay)
            await asyncio.sleep(delay)
            remaining = failed
        SEARCH_REINDEX_FAILED_OBJECT_TYPES.set(0)
        search_reindex_status = {
            "state": "ok",
            "pending_outside_policy": await _count_outside_policy_or_none(opensearch_url, opensearch_password),
            "total": len(rows), "failed": [], "attempts": attempt,
        }
    except Exception:
        search_reindex_status = {"state": "error"}
        logger.exception("search policy reindex aborted")
        return 0
    logger.info(
        "reindexed %d/%d object types onto search policy_version %s",
        done,
        len(rows),
        search.POLICY_VERSION,
    )
    return done


async def _count_outside_policy_or_none(opensearch_url: str, opensearch_password: str) -> int | None:
    try:
        count = await search.count_outside_policy(opensearch_url, opensearch_password)
    except Exception:
        logger.exception("search policy version check failed")
        SEARCH_DOCUMENTS_OUTSIDE_POLICY.set(-1)
        return None
    SEARCH_DOCUMENTS_OUTSIDE_POLICY.set(count)
    return count


def _record_skipped_invalid(tenant_id: str, object_type_urn: str, object_type_name: str, count: int) -> None:
    """Latest snapshot wins: each ingest or reindex replaces the type's count."""
    if count:
        search_skipped_invalid[object_type_urn] = count
    else:
        search_skipped_invalid.pop(object_type_urn, None)
    SEARCH_ROWS_SKIPPED_INVALID.labels(tenant=tenant_id, object_type=object_type_name).set(count)
