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
import logging

import asyncpg

from holon_common import Classification, EventConsumer, most_restrictive

from . import lineage, ontology, resolver, search, serving_store

logger = logging.getLogger("knowledge.catalog")

CUSTOMERS_COLUMN_CLASSIFICATION: dict[str, Classification] = {
    "id": Classification.PUBLIC,
    "name": Classification.INTERNAL,
    "email": Classification.CONFIDENTIAL,
    "country": Classification.INTERNAL,
    "segment": Classification.INTERNAL,
    "lifetime_value": Classification.CONFIDENTIAL,
    "updated_at": Classification.INTERNAL,
}

ORDERS_COLUMN_CLASSIFICATION: dict[str, Classification] = {
    "id": Classification.PUBLIC,
    "customer_id": Classification.INTERNAL,
    "product": Classification.INTERNAL,
    "amount": Classification.CONFIDENTIAL,
    "status": Classification.INTERNAL,
    "ordered_at": Classification.INTERNAL,
}

# No confidential column here — most_restrictive() lands on internal, the
# deliberate contrast to Customer/Order (both confidential): the mechanism
# isn't hardwired to one outcome, it reflects whatever the data actually is.
SUPPORT_TICKETS_COLUMN_CLASSIFICATION: dict[str, Classification] = {
    "id": Classification.PUBLIC,
    "customer_id": Classification.INTERNAL,
    "subject": Classification.INTERNAL,
    "status": Classification.INTERNAL,
    "priority": Classification.PUBLIC,
    "created_at": Classification.INTERNAL,
}

# Every column public — published reviews. most_restrictive() over an
# all-public set correctly yields public: no special case needed, just
# what the same computation produces from different input data. First
# time this build actually reaches the public tier.
PRODUCT_REVIEWS_COLUMN_CLASSIFICATION: dict[str, Classification] = {
    "id": Classification.PUBLIC,
    "order_id": Classification.PUBLIC,
    "rating": Classification.PUBLIC,
    "comment": Classification.PUBLIC,
    "reviewer_name": Classification.PUBLIC,
    "reviewed_at": Classification.PUBLIC,
}

# No confidential column — supplier master data, same tier as
# SupportTicket. The fourth connector (file import) landing on the
# same "internal" tier as the second (MongoDB) is expected: classification
# tracks the data's actual sensitivity, not which connector produced it.
SUPPLIERS_COLUMN_CLASSIFICATION: dict[str, Classification] = {
    "id": Classification.PUBLIC,
    "name": Classification.INTERNAL,
    "country": Classification.INTERNAL,
    "category": Classification.INTERNAL,
}

# The fifth connector (streaming) — no confidential column, same
# tier as SupportTicket/Supplier.
INVENTORY_LEVELS_COLUMN_CLASSIFICATION: dict[str, Classification] = {
    "id": Classification.PUBLIC,
    "warehouse": Classification.INTERNAL,
    "quantity": Classification.INTERNAL,
    "updated_at": Classification.INTERNAL,
}

# One entry per dataset this connector produces — everything
# `_catalogue_sync` needs to catalogue it and propagate its lineage/
# classification into the right ObjectType.
DATASET_OBJECT_TYPES = {
    "customers": {
        "object_type_urn": ontology.customer_object_type_urn,
        "object_type_name": "Customer",
        "property_mapping": ontology.CUSTOMER_PROPERTY_MAPPING,
        "column_classification": CUSTOMERS_COLUMN_CLASSIFICATION,
        "fetch_all": resolver.fetch_customers,
    },
    "orders": {
        "object_type_urn": ontology.order_object_type_urn,
        "object_type_name": "Order",
        "property_mapping": ontology.ORDER_PROPERTY_MAPPING,
        "column_classification": ORDERS_COLUMN_CLASSIFICATION,
        "fetch_all": resolver.fetch_orders,
    },
    "support_tickets": {
        "object_type_urn": ontology.support_ticket_object_type_urn,
        "object_type_name": "SupportTicket",
        "property_mapping": ontology.SUPPORT_TICKET_PROPERTY_MAPPING,
        "column_classification": SUPPORT_TICKETS_COLUMN_CLASSIFICATION,
        "fetch_all": resolver.fetch_support_tickets,
    },
    "reviews": {
        "object_type_urn": ontology.product_review_object_type_urn,
        "object_type_name": "ProductReview",
        "property_mapping": ontology.PRODUCT_REVIEW_PROPERTY_MAPPING,
        "column_classification": PRODUCT_REVIEWS_COLUMN_CLASSIFICATION,
        "fetch_all": resolver.fetch_reviews,
    },
    "suppliers": {
        "object_type_urn": ontology.supplier_object_type_urn,
        "object_type_name": "Supplier",
        "property_mapping": ontology.SUPPLIER_PROPERTY_MAPPING,
        "column_classification": SUPPLIERS_COLUMN_CLASSIFICATION,
        "fetch_all": resolver.fetch_suppliers,
    },
    "inventory_levels": {
        "object_type_urn": ontology.inventory_level_object_type_urn,
        "object_type_name": "InventoryLevel",
        "property_mapping": ontology.INVENTORY_LEVEL_PROPERTY_MAPPING,
        "column_classification": INVENTORY_LEVELS_COLUMN_CLASSIFICATION,
        "fetch_all": resolver.fetch_inventory_levels,
    },
}

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
    """Replay needs the *historical* snapshot a frozen plan pinned
    to, not just the latest one `latest_dataset_version_urn` returns.
    """
    row = await pool.fetchrow("SELECT * FROM dataset_version WHERE urn = $1", dataset_version_urn)
    return dict(row) if row else None


async def latest_dataset_version_urn(pool: asyncpg.Pool, dataset_urn: str) -> str | None:
    """Used by `execution.py` to pin an ExecutionPlan's inputs to the exact
    DatasetVersion it reads — the plan hash changes whenever this
    does, which is what makes the plan-hash cache self-invalidating
    on real data changes, with no explicit invalidation logic needed.
    """
    row = await pool.fetchrow(
        "SELECT urn FROM dataset_version WHERE dataset_urn = $1 ORDER BY created_at DESC LIMIT 1",
        dataset_urn,
    )
    return row["urn"] if row else None


async def _catalogue_sync(conn: asyncpg.Connection, tenant_id: str, workspace_id: str, payload: dict) -> None:
    dataset_name = payload["dataset_name"]

    # Dataset/DatasetVersion cataloguing is generic — every sync gets one,
    # ObjectType-mapped or not.
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

    spec = DATASET_OBJECT_TYPES.get(dataset_name)
    if spec is None:
        # A Connector *plugin* (`plugin_registry.py`) can sync a dataset Knowledge has no
        # ontology mapping for — a real, expected state, not a bug.
        logger.info("dataset %r synced with no ObjectType mapping — catalogued, not yet an ObjectType", dataset_name)
        return

    object_type_urn = spec["object_type_urn"](tenant_id, workspace_id)
    property_mapping = spec["property_mapping"]
    column_classification = spec["column_classification"]

    await lineage.record_edge(conn, tenant_id, payload["dataset_version_urn"], object_type_urn, "maps_to")

    # Column-level lineage + classification propagation, both
    # derived from the same mapping — never hand-declared on the ObjectType.
    # The per-property classification that `most_restrictive()` below
    # collapses into one ObjectType-wide value is *also* persisted per
    # property here, so read time can mask individual confidential fields
    # instead of only ever allowing or denying the whole object. Keyed by
    # `source_column` (e.g. `lifetime_value`), not `property_name` (e.g.
    # `lifetimeValue`): `resolver.py`/`serving_store.py` serve rows with
    # their raw source column names verbatim — `property_mapping`'s
    # camelCase keys are used for lineage tracking only, never applied as
    # runtime row keys.
    for prop_name, source_col in property_mapping.items():
        col_cls = column_classification.get(source_col, Classification.INTERNAL)
        await lineage.record_edge(
            conn, tenant_id, payload["dataset_version_urn"], object_type_urn, "maps_to",
            source_column=source_col, target_property=prop_name,
        )
        await ontology.upsert_property_classification(conn, object_type_urn, source_col, col_cls.value)

    overall_classification = most_restrictive(*column_classification.values())
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
    """Serving store materialization — one scan per sync,
    not one per read. Deliberately its own connection/transaction, acquired
    *after* `_catalogue_sync` already committed: the Iceberg/DuckDB scan
    inside `fetch_all` is slow and holds no lock of its own, so running it
    inside a Postgres transaction would tie up a pool connection (Knowledge's
    pool is only `max_size=5`) for the scan's full duration. If this step
    fails or lags, `main._resolve_one`/`_resolve_many` already degrade to a
    live federated read — cataloguing was never conditional on
    materialization succeeding, and it doesn't need to be.

    Also indexes the same rows into OpenSearch — the same scan, one
    extra sink, not a second one.
    """
    dataset_name = payload["dataset_name"]
    spec = DATASET_OBJECT_TYPES.get(dataset_name)
    if spec is None:
        return
    rows = await asyncio.to_thread(spec["fetch_all"], **iceberg_config)
    async with pool.acquire() as conn, conn.transaction():
        await serving_store.materialize(
            conn,
            object_type=spec["object_type_name"],
            tenant_id=tenant_id,
            snapshot_id=payload["snapshot_id"],
            rows=rows,
        )

    object_type_urn = spec["object_type_urn"](tenant_id, workspace_id)
    object_type = await ontology.get_object_type(pool, object_type_urn)
    await search.index_rows(
        opensearch_url,
        opensearch_password,
        object_type_name=spec["object_type_name"],
        tenant_id=tenant_id,
        classification=object_type["classification"],
        property_mapping=spec["property_mapping"],
        rows=rows,
        allowed_countries=allowed_countries,
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
    """A single unhandled exception here must never permanently kill the
    consumer task — that would silently stop cataloguing everything
    published afterwards, not just the one bad event. Log loudly and keep
    consuming; a poison message still gets its offset committed rather
    than wedging the whole pipeline.
    """
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
