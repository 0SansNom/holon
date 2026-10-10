"""Join-link materialization and relation backfill for the Knowledge catalog."""
from __future__ import annotations

import asyncio
import logging
import time

import asyncpg

from . import catalog_state, link_overlays, ontology, relation_links, resolver
from .catalog_state import (
    _ENSURE_MISS_TTL_SECONDS,
    _ensure_locks,
    _ensure_miss_until,
)

from .catalog_errors import TransientCatalogError

logger = logging.getLogger("knowledge.catalog")


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
    catalog_state.join_link_backfill_status = "running"
    try:
        relations = await ontology.list_join_dataset_relation_types(pool)
        for relation in relations:
            try:
                await refresh_join_links_for_relation(pool, relation, iceberg_config)
            except Exception:
                logger.exception("join_link backfill failed for %s", relation.get("urn"))
        catalog_state.join_link_backfill_status = "ok"
    except Exception:
        catalog_state.join_link_backfill_status = "error"
        logger.exception("join_link backfill aborted")

