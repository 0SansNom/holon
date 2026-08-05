"""Conceptual ontology — seeded ObjectTypes and RelationTypes.

ObjectTypes are seeded at startup from code, but no longer *only*
editable that way: **ontology lifecycle** (versioning/publication) is real.
`propose_object_type_version` creates a `draft` in `object_type_version`
(its own append-only history, one row per version); `publish_object_type_version`
is the only thing that ever updates the live `object_type` row everything else reads
(`resolver.py`/`serving_store.py`/`search.py`/every endpoint) — same
draft-then-promote discipline as Application Builder's own versioning,
and the same workspace-`approve` governance gate `create_relation_type` already uses.
RelationTypes are a partial exception in a different way: `create_relation_type` lets a workspace
admin register new ones at runtime directly (no draft step — cardinality/
endpoint validation is enforced synchronously), but only as *definitions*;
a newly-registered relation type is not wired into any traversal endpoint,
the seeded ones below are still the ones `main.py` knows how to traverse.

`ensure_seeded` runs its own `_upsert_object_type` on every startup, refreshing
`description`/`property_mapping` from code — necessary so the bootstrap
seed data itself stays a real, editable source of truth.
The upsert only ever overwrites a row still at `version = 1` (never governance-published) —
once `publish_object_type_version` bumps a row past that, the boot-time
reseed leaves it alone.

`classification` is seeded as `internal` — meaning "no
lineage computed yet" — and is then owned by `catalog.py`, which
recomputes it as `most_restrictive()` of the mapped source columns every
time a DatasetVersion is catalogued. It is deliberately excluded
from the object_type upsert's `ON CONFLICT DO UPDATE` so that restarting
this service never clobbers a value already computed from real lineage.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

import asyncpg

from holon_common import EventActor, EventEnvelope, build_urn, outbox
from holon_common.authz import PermissionClient

CUSTOMER_PROPERTY_MAPPING = {
    "id": "id",
    "name": "name",
    "email": "email",
    "country": "country",
    "segment": "segment",
    "lifetimeValue": "lifetime_value",
    "updatedAt": "updated_at",
}

ORDER_PROPERTY_MAPPING = {
    "id": "id",
    "customerId": "customer_id",
    "product": "product",
    "amount": "amount",
    "status": "status",
    "orderedAt": "ordered_at",
}

SUPPORT_TICKET_PROPERTY_MAPPING = {
    "id": "id",
    "customerId": "customer_id",
    "subject": "subject",
    "status": "status",
    "priority": "priority",
    "createdAt": "created_at",
}

PRODUCT_REVIEW_PROPERTY_MAPPING = {
    "id": "id",
    "orderId": "order_id",
    "rating": "rating",
    "comment": "comment",
    "reviewerName": "reviewer_name",
    "reviewedAt": "reviewed_at",
}

SUPPLIER_PROPERTY_MAPPING = {
    "id": "id",
    "name": "name",
    "country": "country",
    "category": "category",
}

# `id` holds the SKU string directly (the streaming connector aliases it
# that way so `serving_store.materialize`'s hardcoded `row["id"]` key
# works unchanged) — the first ObjectType in this build keyed by a
# non-integer instance id.
INVENTORY_LEVEL_PROPERTY_MAPPING = {
    "id": "id",
    "warehouse": "warehouse",
    "quantity": "quantity",
    "updatedAt": "updated_at",
}

# All many_to_one. Explicit cardinality and direction — both are
# spelled out, not implied. ProductReview.order targets Order, not Customer —
# the graph now chains one hop further: Customer <- Order <- ProductReview.
RELATION_TYPES = [
    {
        "name": "Order.customer",
        "source_object_type": "Order",
        "target_object_type": "Customer",
        "source_property": "customerId",
        "cardinality": "many_to_one",
    },
    {
        "name": "SupportTicket.customer",
        "source_object_type": "SupportTicket",
        "target_object_type": "Customer",
        "source_property": "customerId",
        "cardinality": "many_to_one",
    },
    {
        "name": "ProductReview.order",
        "source_object_type": "ProductReview",
        "target_object_type": "Order",
        "source_property": "orderId",
        "cardinality": "many_to_one",
    },
]

INITIAL_CLASSIFICATION = "internal"

DDL = """
CREATE TABLE IF NOT EXISTS object_type (
    urn TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    source_dataset_urn TEXT NOT NULL,
    property_mapping JSONB NOT NULL,
    classification TEXT NOT NULL DEFAULT 'internal',
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- additive migrations for databases seeded before these columns existed
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS classification TEXT NOT NULL DEFAULT 'internal';
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '';
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS version INT NOT NULL DEFAULT 1;

-- Ontology lifecycle (versioning/publication) — one row per proposed or
-- published version, append-only. `object_type` above always mirrors the
-- current *published* version only; a draft here never affects it.
CREATE TABLE IF NOT EXISTS object_type_version (
    id BIGSERIAL PRIMARY KEY,
    object_type_urn TEXT NOT NULL REFERENCES object_type(urn),
    tenant_id TEXT NOT NULL,
    version INT NOT NULL,
    property_mapping JSONB NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ,
    UNIQUE (object_type_urn, version)
);

CREATE TABLE IF NOT EXISTS relation_type (
    urn TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    source_object_type_urn TEXT NOT NULL,
    target_object_type_urn TEXT NOT NULL,
    source_property TEXT NOT NULL,
    cardinality TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Row/column security declared per property, not just collapsed
-- into one ObjectType-wide `object_type.classification` value. Populated
-- by `catalog._catalogue_sync` from the same `column_classification`
-- mapping that already computes the aggregate value via
-- `most_restrictive()` — this is the per-property detail that
-- computation was discarding.
CREATE TABLE IF NOT EXISTS object_type_property (
    object_type_urn TEXT NOT NULL REFERENCES object_type(urn),
    property_name TEXT NOT NULL,
    classification TEXT NOT NULL,
    PRIMARY KEY (object_type_urn, property_name)
);
"""


VALID_CARDINALITIES = {"one_to_one", "one_to_many", "many_to_one", "many_to_many"}


def object_type_urn(tenant_id: str, workspace_id: str, name: str) -> str:
    return build_urn(tenant_id, workspace_id, "object-type", name)


def relation_type_urn(tenant_id: str, workspace_id: str, name: str) -> str:
    return build_urn(tenant_id, workspace_id, "relation-type", name)


def customer_object_type_urn(tenant_id: str, workspace_id: str) -> str:
    return object_type_urn(tenant_id, workspace_id, "Customer")


def order_object_type_urn(tenant_id: str, workspace_id: str) -> str:
    return object_type_urn(tenant_id, workspace_id, "Order")


def support_ticket_object_type_urn(tenant_id: str, workspace_id: str) -> str:
    return object_type_urn(tenant_id, workspace_id, "SupportTicket")


def product_review_object_type_urn(tenant_id: str, workspace_id: str) -> str:
    return object_type_urn(tenant_id, workspace_id, "ProductReview")


def supplier_object_type_urn(tenant_id: str, workspace_id: str) -> str:
    return object_type_urn(tenant_id, workspace_id, "Supplier")


def inventory_level_object_type_urn(tenant_id: str, workspace_id: str) -> str:
    return object_type_urn(tenant_id, workspace_id, "InventoryLevel")


def workspace_urn(tenant_id: str, workspace_id: str) -> str:
    return build_urn(tenant_id, "global", "workspace", workspace_id)


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


async def _upsert_object_type(
    pool: asyncpg.Pool, urn: str, tenant_id: str, name: str, source_dataset_urn: str, mapping: dict, description: str
) -> None:
    """`description`:
    a mandatory natural-language description of what this ObjectType *is*,
    reviewed like code, refreshed from source truth on every startup — same
    treatment as `property_mapping`. Not excluded from the upsert the way
    `classification` deliberately is (that one is computed lineage, owned by
    `catalog.py`, never clobbered by a restart); a description is authored
    metadata, restart-safe to always refresh — *unless* `publish_object_type_version`
    has already moved this row past `version = 1` (a real governance
    change), in which case the boot-time reseed leaves it alone. See
    module docstring for why this guard exists.
    """
    await pool.execute(
        """
        INSERT INTO object_type (urn, tenant_id, name, source_dataset_urn, property_mapping, classification, description)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
        ON CONFLICT (urn) DO UPDATE SET
            source_dataset_urn = CASE WHEN object_type.version = 1 THEN EXCLUDED.source_dataset_urn ELSE object_type.source_dataset_urn END,
            property_mapping = CASE WHEN object_type.version = 1 THEN EXCLUDED.property_mapping ELSE object_type.property_mapping END,
            description = CASE WHEN object_type.version = 1 THEN EXCLUDED.description ELSE object_type.description END
        """,
        urn,
        tenant_id,
        name,
        source_dataset_urn,
        json.dumps(mapping),
        INITIAL_CLASSIFICATION,
        description,
    )


async def ensure_seeded(pool: asyncpg.Pool, tenant_id: str, workspace_id: str) -> None:
    await _upsert_object_type(
        pool,
        customer_object_type_urn(tenant_id, workspace_id),
        tenant_id,
        "Customer",
        build_urn(tenant_id, workspace_id, "dataset", "customers"),
        CUSTOMER_PROPERTY_MAPPING,
        "A business account that buys from us — company name, contact email, "
        "country, commercial segment (enterprise/mid-market/smb), and lifetime "
        "value. The root of the customer-facing object graph: orders, support "
        "tickets and product reviews all trace back to one.",
    )
    await _upsert_object_type(
        pool,
        order_object_type_urn(tenant_id, workspace_id),
        tenant_id,
        "Order",
        build_urn(tenant_id, workspace_id, "dataset", "orders"),
        ORDER_PROPERTY_MAPPING,
        "A single purchase placed by a Customer — product, amount, fulfillment "
        "status (pending/shipped/delivered), and order date. Many orders per "
        "customer; product reviews reference the order they were left against.",
    )
    await _upsert_object_type(
        pool,
        support_ticket_object_type_urn(tenant_id, workspace_id),
        tenant_id,
        "SupportTicket",
        build_urn(tenant_id, workspace_id, "dataset", "support_tickets"),
        SUPPORT_TICKET_PROPERTY_MAPPING,
        "A customer support request — subject line, open/closed status, "
        "priority, and which Customer raised it. Sourced from the support "
        "desk's MongoDB collection, not the same system as Customer/Order.",
    )
    await _upsert_object_type(
        pool,
        product_review_object_type_urn(tenant_id, workspace_id),
        tenant_id,
        "ProductReview",
        build_urn(tenant_id, workspace_id, "dataset", "reviews"),
        PRODUCT_REVIEW_PROPERTY_MAPPING,
        "A public product review left against a specific Order — star rating, "
        "free-text comment, reviewer display name. The only ObjectType whose "
        "data is entirely public (no confidential columns).",
    )
    await _upsert_object_type(
        pool,
        supplier_object_type_urn(tenant_id, workspace_id),
        tenant_id,
        "Supplier",
        build_urn(tenant_id, workspace_id, "dataset", "suppliers"),
        SUPPLIER_PROPERTY_MAPPING,
        "A vendor we source materials/components from — name, country, and "
        "procurement category (raw-materials/components/electronics/packaging). "
        "Standalone: not yet related to any other ObjectType in this build.",
    )
    await _upsert_object_type(
        pool,
        inventory_level_object_type_urn(tenant_id, workspace_id),
        tenant_id,
        "InventoryLevel",
        build_urn(tenant_id, workspace_id, "dataset", "inventory_levels"),
        INVENTORY_LEVEL_PROPERTY_MAPPING,
        "The current on-hand quantity of one SKU at one warehouse — the only "
        "ObjectType fed by continuous streaming ingestion rather than a "
        "periodic batch sync, so its data changes independently of any /sync call.",
    )

    for relation in RELATION_TYPES:
        await pool.execute(
            """
            INSERT INTO relation_type (urn, tenant_id, name, source_object_type_urn, target_object_type_urn, source_property, cardinality)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (urn) DO NOTHING
            """,
            build_urn(tenant_id, workspace_id, "relation-type", relation["name"]),
            tenant_id,
            relation["name"],
            object_type_urn(tenant_id, workspace_id, relation["source_object_type"]),
            object_type_urn(tenant_id, workspace_id, relation["target_object_type"]),
            relation["source_property"],
            relation["cardinality"],
        )


async def get_object_type(pool: asyncpg.Pool, urn: str) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM object_type WHERE urn = $1", urn)
    if row is None:
        return None
    result = dict(row)
    # asyncpg returns JSONB columns as raw text, not parsed — a real,
    # pre-existing gap in this specific reader (every other JSONB reader
    # in this codebase already does this, e.g. serving_store.get_instance)
    # caught while building ontology versioning: `GET /ontology/{name}`
    # had been silently handing callers a JSON *string* for
    # `property_mapping` instead of an object this whole time.
    if isinstance(result["property_mapping"], str):
        result["property_mapping"] = json.loads(result["property_mapping"])
    return result


def _parse_version_row(row: asyncpg.Record) -> dict:
    result = dict(row)
    if isinstance(result["property_mapping"], str):
        result["property_mapping"] = json.loads(result["property_mapping"])
    return result


async def list_object_type_versions(pool: asyncpg.Pool, object_type_urn: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM object_type_version WHERE object_type_urn = $1 ORDER BY version DESC", object_type_urn
    )
    return [_parse_version_row(row) for row in rows]


async def get_object_type_version(pool: asyncpg.Pool, object_type_urn: str, version: int) -> Optional[dict]:
    row = await pool.fetchrow(
        "SELECT * FROM object_type_version WHERE object_type_urn = $1 AND version = $2", object_type_urn, version
    )
    return _parse_version_row(row) if row else None


async def propose_object_type_version(
    pool: asyncpg.Pool,
    *,
    object_type_urn: str,
    property_mapping: Optional[dict] = None,
    description: Optional[str] = None,
) -> dict:
    """Creates a `draft` version — never touches the live `object_type`
    row (everything else in this build keeps reading the current
    *published* state until `publish_object_type_version` says
    otherwise). A partial update (only `description`, say) carries the
    current published value forward for whatever isn't overridden, so
    proposing a version never silently blanks out the other field.
    """
    current = await get_object_type(pool, object_type_urn)
    if current is None:
        raise ValueError(f"unknown ObjectType: {object_type_urn}")

    next_version = current["version"] + 1
    new_mapping = property_mapping if property_mapping is not None else current["property_mapping"]
    if isinstance(new_mapping, str):
        new_mapping = json.loads(new_mapping)
    new_description = description if description is not None else current["description"]

    await pool.execute(
        """
        INSERT INTO object_type_version (object_type_urn, tenant_id, version, property_mapping, description, status)
        VALUES ($1, $2, $3, $4::jsonb, $5, 'draft')
        """,
        object_type_urn, current["tenant_id"], next_version, json.dumps(new_mapping), new_description,
    )
    return await get_object_type_version(pool, object_type_urn, next_version)


async def publish_object_type_version(pool: asyncpg.Pool, *, object_type_urn: str, version: int) -> dict:
    """The only thing that ever updates the live `object_type` row past
    its bootstrap state — every other reader in this build
    (`resolver.py`, `serving_store.py`, `search.py`, every `/objects/...`
    endpoint) keeps working unchanged, since they all read `object_type`
    as before. Publishes `knowledge.objecttype.published` (transactional outbox).
    """
    draft = await get_object_type_version(pool, object_type_urn, version)
    if draft is None:
        raise ValueError(f"no version {version} found for {object_type_urn}")
    if draft["status"] == "published":
        raise ValueError(f"version {version} of {object_type_urn} is already published")

    current = await get_object_type(pool, object_type_urn)
    previous_version = current["version"] if current else None

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE object_type_version SET status = 'published', published_at = now() WHERE object_type_urn = $1 AND version = $2",
            object_type_urn, version,
        )
        await conn.execute(
            """
            UPDATE object_type SET version = $1, property_mapping = $2::jsonb, description = $3
            WHERE urn = $4
            """,
            version, json.dumps(draft["property_mapping"]), draft["description"], object_type_urn,
        )
        event_id = uuid.uuid4().hex
        event = EventEnvelope(
            event_id=event_id,
            event_type="knowledge.objecttype.published",
            tenant_id=draft["tenant_id"],
            aggregate_type="ObjectType",
            aggregate_id=object_type_urn,
            correlation_id=event_id,
            partition_key=f"{draft['tenant_id']}/{object_type_urn}",
            producer="knowledge-platform@0.1.0",
            actor=EventActor(type="service_account", urn=build_urn(draft["tenant_id"], "global", "service-account", "ontology-governance")),
            payload={
                "object_type_urn": object_type_urn,
                "name": current["name"] if current else object_type_urn,
                "version": version,
                "previous_version": previous_version,
            },
        )
        await outbox.enqueue(conn, event)

    return await get_object_type(pool, object_type_urn)


async def upsert_property_classification(
    conn: asyncpg.Connection, object_type_urn: str, property_name: str, classification: str
) -> None:
    await conn.execute(
        """
        INSERT INTO object_type_property (object_type_urn, property_name, classification)
        VALUES ($1, $2, $3)
        ON CONFLICT (object_type_urn, property_name) DO UPDATE SET classification = EXCLUDED.classification
        """,
        object_type_urn, property_name, classification,
    )


async def get_property_classifications(pool: asyncpg.Pool, object_type_urn: str) -> dict[str, str]:
    rows = await pool.fetch(
        "SELECT property_name, classification FROM object_type_property WHERE object_type_urn = $1", object_type_urn
    )
    return {row["property_name"]: row["classification"] for row in rows}


async def get_relation_type(pool: asyncpg.Pool, urn: str) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM relation_type WHERE urn = $1", urn)
    return dict(row) if row else None


async def list_object_types(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    """A real, previously-missing gap: `RelationType` always had
    `list_relation_types`/`GET /relation-types`, but `ObjectType` never
    got the equivalent — every existing caller already knew the six
    hardcoded type names. A genuine Object Explorer UI needs to discover
    them, not hardcode the same list a second time client-side.
    """
    rows = await pool.fetch("SELECT * FROM object_type WHERE tenant_id = $1 ORDER BY name", tenant_id)
    results = []
    for row in rows:
        result = dict(row)
        if isinstance(result["property_mapping"], str):
            result["property_mapping"] = json.loads(result["property_mapping"])
        results.append(result)
    return results


async def list_relation_types(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM relation_type WHERE tenant_id = $1 ORDER BY name", tenant_id)
    return [dict(row) for row in rows]


async def create_relation_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    source_object_type: str,
    target_object_type: str,
    source_property: str,
    cardinality: str,
) -> dict:
    """Explicit cardinality and direction with existing endpoints
    enforced here rather than merely true by
    construction of the hardcoded `RELATION_TYPES` seed list. Definition
    only: this does not wire the new relation into any traversal endpoint
    (the three existing ones in `main.py` stay hand-written).
    """
    if cardinality not in VALID_CARDINALITIES:
        raise ValueError(f"invalid cardinality: {cardinality!r} (must be one of {sorted(VALID_CARDINALITIES)})")

    source_urn = object_type_urn(tenant_id, workspace_id, source_object_type)
    if await get_object_type(pool, source_urn) is None:
        raise ValueError(f"source_object_type does not exist: {source_object_type}")

    target_urn = object_type_urn(tenant_id, workspace_id, target_object_type)
    if await get_object_type(pool, target_urn) is None:
        raise ValueError(f"target_object_type does not exist: {target_object_type}")

    urn = relation_type_urn(tenant_id, workspace_id, name)
    await pool.execute(
        """
        INSERT INTO relation_type (urn, tenant_id, name, source_object_type_urn, target_object_type_urn, source_property, cardinality)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        urn,
        tenant_id,
        name,
        source_urn,
        target_urn,
        source_property,
        cardinality,
    )
    return await get_relation_type(pool, urn)


async def ensure_authz_seeded(client: PermissionClient, schema_path: str, tenant_id: str, workspace_id: str) -> None:
    """Knowledge owns ObjectType, so it links its own resources under
    the workspace itself — Identity only owns the tenant/workspace side of
    the graph. `write_schema` is idempotent: calling it again with the same
    file is a no-op, and removes any dependency on Identity having started
    first (see PermissionClient docstring in identity/app/main.py).
    """
    await client.write_schema(Path(schema_path).read_text())
    w_urn = workspace_urn(tenant_id, workspace_id)
    for name in ("Customer", "Order", "SupportTicket", "ProductReview", "Supplier", "InventoryLevel"):
        await client.write_relationship(
            resource_type="object_type",
            resource_urn=object_type_urn(tenant_id, workspace_id, name),
            relation="parent_workspace",
            subject_type="workspace",
            subject_urn=w_urn,
        )
