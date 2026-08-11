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

    #  (Pipeline/Transform DAG): a pipeline TransformStep's output
    # names the input DatasetVersion it was computed from
    # (`source_dataset_version_urn`) — every ordinary connector sync
    # omits this, since it has no upstream dataset, only a source system.
    # Recording this edge here, in the *same* consumer path every sync
    # already goes through, is what makes the DAG's lineage genuinely
    # automatic (captured from execution) rather than hand-declared —
    # the same principle this module's own docstring already states for
    # `maps_to` edges below, just one more `relation`.
    source_dataset_version_urn = payload.get("source_dataset_version_urn")
    if source_dataset_version_urn:
        await lineage.record_edge(
            conn, tenant_id, source_dataset_version_urn, payload["dataset_version_urn"], "derived_from"
        )

    spec = DATASET_OBJECT_TYPES.get(dataset_name)
    if spec is not None:
        object_type_urn = spec["object_type_urn"](tenant_id, workspace_id)
        property_mapping = spec["property_mapping"]
        column_classification = spec["column_classification"]
    else:
        # Not one of the six boot-known types — check the *dynamic*
        # registry next (`ontology.create_object_type`, the self-serve
        # path: map an already-synced Dataset to an ObjectType by name).
        # A Connector plugin can also sync a dataset with no ObjectType
        # mapping at all yet — an expected, honestly-logged state either
        # way, not an error.
        dynamic_type = await ontology.get_object_type_by_dataset(conn, tenant_id, payload["dataset_urn"])
        if dynamic_type is None:
            logger.info("dataset %r synced with no ObjectType mapping — catalogued, not yet an ObjectType", dataset_name)
            return
        object_type_urn = dynamic_type["urn"]
        property_mapping = dynamic_type["property_mapping"]
        # The admin's own declared classification (`ontology.create_object_type`'s
        # `column_classification` arg, persisted on `object_type` — the
        # self-serve equivalent of a `CUSTOMERS_COLUMN_CLASSIFICATION`-style
        # constant). A column the admin never classified still defaults
        # to internal, not silently public — same fallback the loop below
        # already applies to any *individual* missing column via
        # `.get(..., Classification.INTERNAL)`.
        column_classification = {
            col: Classification(value)
            for col, value in (dynamic_type.get("column_classification") or {}).items()
        }

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
    # `column_classification` can be sparse (a self-serve type's admin
    # may not have classified every column, or none at all) — resolve
    # every *mapped* column's effective value up front, defaulting
    # unclassified ones to internal, so both the per-property write below
    # and the `most_restrictive` aggregate see the same complete set
    # rather than `most_restrictive` ever being called with zero values.
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

    # `property_mapping` itself being empty (a self-serve type created
    # with every property name blanked out) is the one case
    # `effective_classification` can still end up empty despite the
    # default above — `most_restrictive()` requires at least one value.
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
    if spec is not None:
        object_type_name = spec["object_type_name"]
        object_type_urn = spec["object_type_urn"](tenant_id, workspace_id)
        property_mapping = spec["property_mapping"]
        fetch_all = spec["fetch_all"]
    else:
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
    await search.index_rows(
        opensearch_url,
        opensearch_password,
        object_type_name=object_type_name,
        tenant_id=tenant_id,
        classification=object_type["classification"],
        property_mapping=property_mapping,
        rows=rows,
        allowed_countries=allowed_countries,
        property_types=object_type.get("property_types") or {},
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
