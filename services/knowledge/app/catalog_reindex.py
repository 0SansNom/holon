"""Search reindex helpers for the Knowledge catalog."""
from __future__ import annotations

import asyncio
import logging
import time

import asyncpg

from . import catalog_state, ontology, search, serving_store
from .search_metrics import (
    SEARCH_DOCUMENTS_OUTSIDE_POLICY,
    SEARCH_REINDEX_FAILED_OBJECT_TYPES,
    SEARCH_ROWS_SKIPPED_INVALID,
)

logger = logging.getLogger("knowledge.catalog")


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
    retry_seconds: tuple[float, ...] = catalog_state._SEARCH_REINDEX_RETRY_SECONDS,
) -> int:
    """Rewrite search documents that are still on an older policy version.

    Already-current indexes are left alone. Rewrites overwrite by document
    id and do not delete first, so a crash cannot empty a type. Failures
    on one type do not stop the others; failed types are retried with
    backoff until they succeed. Progress is published in
    ``search_reindex_status`` for ``/ready``.
    """
    try:
        pending = await _count_outside_policy_or_none(opensearch_url, opensearch_password)
        if pending == 0:
            catalog_state.search_reindex_status = {"state": "ok", "pending_outside_policy": 0, "failed": []}
            logger.info("search index already at policy_version %s", search.POLICY_VERSION)
            return 0
        rows = await pool.fetch("SELECT urn, name, tenant_id FROM object_type ORDER BY tenant_id, name")
        remaining = list(rows)
        done = 0
        attempt = 0
        catalog_state.search_reindex_status = {"state": "running", "pending_outside_policy": pending, "total": len(rows), "failed": []}
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
            catalog_state.search_reindex_status = {
                "state": "degraded", "pending_outside_policy": pending, "total": len(rows),
                "failed": [row["urn"] for row in failed], "attempts": attempt,
            }
            SEARCH_REINDEX_FAILED_OBJECT_TYPES.set(len(failed))
            logger.warning("search policy reindex: %d object types failed, retrying in %ss", len(failed), delay)
            await asyncio.sleep(delay)
            remaining = failed
        SEARCH_REINDEX_FAILED_OBJECT_TYPES.set(0)
        catalog_state.search_reindex_status = {
            "state": "ok",
            "pending_outside_policy": await _count_outside_policy_or_none(opensearch_url, opensearch_password),
            "total": len(rows), "failed": [], "attempts": attempt,
        }
    except Exception:
        catalog_state.search_reindex_status = {"state": "error"}
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
        catalog_state.search_skipped_invalid[object_type_urn] = count
    else:
        catalog_state.search_skipped_invalid.pop(object_type_urn, None)
    SEARCH_ROWS_SKIPPED_INVALID.labels(tenant=tenant_id, object_type=object_type_name).set(count)

