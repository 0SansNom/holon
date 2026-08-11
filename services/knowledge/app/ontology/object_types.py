"""ObjectType core: schema DDL, boot-time seeding, and version history
reads. `ensure_seeded` runs its own `_upsert_object_type` on every
startup, refreshing `description`/`property_mapping` from code —
necessary so the bootstrap seed data itself stays a real, editable
source of truth. The upsert only ever overwrites a row still at
`version = 1` (never governance-published) — once `publish_object_type_version`
(in `publishing.py`) bumps a row past that, the boot-time reseed leaves
it alone.
"""

from __future__ import annotations

import json
from typing import Optional

import asyncpg

from holon_common import Classification, build_urn, most_restrictive

from .urns import (
    customer_object_type_urn,
    inventory_level_object_type_urn,
    object_type_urn,
    order_object_type_urn,
    product_review_object_type_urn,
    support_ticket_object_type_urn,
    supplier_object_type_urn,
)

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
        "target_property": "orders",
        "cardinality": "many_to_one",
    },
    {
        "name": "SupportTicket.customer",
        "source_object_type": "SupportTicket",
        "target_object_type": "Customer",
        "source_property": "customerId",
        "target_property": "tickets",
        "cardinality": "many_to_one",
    },
    {
        "name": "ProductReview.order",
        "source_object_type": "ProductReview",
        "target_object_type": "Order",
        "source_property": "orderId",
        "target_property": "reviews",
        "cardinality": "many_to_one",
    },
]

INITIAL_CLASSIFICATION = "internal"

VALID_LIFECYCLE_STATUSES = frozenset({"experimental", "active", "deprecated"})
VALID_VISIBILITIES = frozenset({"prominent", "normal", "hidden"})

# All JSONB columns on `object_type`; a subset (without `column_classification`)
# covers `object_type_version`. Centralised so adding a new JSONB column only
# requires touching this constant — not the four independent call sites below.
_OT_JSONB_KEYS: tuple[str, ...] = (
    "property_mapping", "implements", "derived_properties", "markings",
    "property_formats", "conditional_formats", "property_types", "column_classification",
)
# object_type_version has no column_classification column.
_OTV_JSONB_KEYS: tuple[str, ...] = _OT_JSONB_KEYS[:-1]

# Scalar Foundry-parity+ metadata mirrored on live + version rows.
_OT_META_KEYS: tuple[str, ...] = (
    "primary_key", "title_key", "plural_display_name", "lifecycle_status", "visibility", "icon",
)


def title_of(instance: dict, object_type: dict | None = None) -> str:
    """Display title for an object instance — title_key, else primary_key, else id/name."""
    keys: list[str] = []
    if object_type:
        if object_type.get("title_key"):
            keys.append(object_type["title_key"])
        if object_type.get("primary_key"):
            keys.append(object_type["primary_key"])
    keys.extend(["name", "id"])
    mapping = (object_type or {}).get("property_mapping") or {}
    for key in keys:
        if key in instance and instance[key] is not None and instance[key] != "":
            return str(instance[key])
        col = mapping.get(key)
        if col and col in instance and instance[col] is not None and instance[col] != "":
            return str(instance[col])
    return str(instance.get("id") or "")


def validate_ot_metadata(
    *,
    property_mapping: dict,
    primary_key: str,
    title_key: str | None,
    lifecycle_status: str,
    visibility: str,
) -> None:
    if lifecycle_status not in VALID_LIFECYCLE_STATUSES:
        raise ValueError(
            f"invalid lifecycle_status: {lifecycle_status!r} (must be one of {sorted(VALID_LIFECYCLE_STATUSES)})"
        )
    if visibility not in VALID_VISIBILITIES:
        raise ValueError(f"invalid visibility: {visibility!r} (must be one of {sorted(VALID_VISIBILITIES)})")
    if not primary_key:
        raise ValueError("primary_key is required")
    if primary_key not in property_mapping:
        raise ValueError(f"primary_key {primary_key!r} must be a key in property_mapping")
    if title_key and title_key not in property_mapping:
        raise ValueError(f"title_key {title_key!r} must be a key in property_mapping")


def _parse_jsonb_keys(row: asyncpg.Record, keys: tuple[str, ...]) -> dict:
    """Deserialise the JSONB columns of an asyncpg record.

    asyncpg may return JSONB columns either as an unparsed string or already
    as a Python object depending on the driver/codec configuration — the
    isinstance guard handles both without failing on either. API callers
    always receive structured dicts/lists, never raw JSON strings.
    """
    result = dict(row)
    for key in keys:
        if isinstance(result.get(key), str):
            result[key] = json.loads(result[key])
    return result


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

-- The reverse-direction accessor name (e.g. `Order.customer`'s
-- `target_property` is `orders`) — Foundry's real Link Type model always
-- names both ends; nullable at the column level only so an in-place
-- upgrade doesn't need a destructive migration for any pre-existing row.
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS target_property TEXT;

-- A purely navigational cluster of ObjectTypes (Foundry's Ontology
-- Manager "Object Type Groups") — no new permission or schema concept,
-- just a named, validated list; `ObjectTypesTab`'s own group filter is
-- what makes this real rather than a label nobody reads.
CREATE TABLE IF NOT EXISTS object_type_group (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    object_types JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
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

-- Polymorphism across ObjectTypes: a named, checked contract (required
-- properties/actions), not just a label. `implements` on `object_type`
-- and `object_type_version` below declares conformance; publish-time
-- validation (`_validate_implements`) is what makes the contract real.
CREATE TABLE IF NOT EXISTS interface_type (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    required_properties JSONB NOT NULL DEFAULT '[]',
    required_actions JSONB NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

ALTER TABLE object_type ADD COLUMN IF NOT EXISTS implements JSONB NOT NULL DEFAULT '[]';
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS implements JSONB NOT NULL DEFAULT '[]';

-- Read-time derived properties: {"tier": "lifetime_tier"} means
-- property `tier`, on every read, is computed by invoking the active
-- Function plugin registered under `function_name="lifetime_tier"` —
-- validated to actually exist at publish time (`_validate_derived_properties`),
-- resolved fresh from `function_registry` at read time (`main.py`) so
-- disabling the backing plugin takes effect immediately, same
-- "nothing cached" discipline every other plugin lookup in this build uses.
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS derived_properties JSONB NOT NULL DEFAULT '{}';
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS derived_properties JSONB NOT NULL DEFAULT '{}';

-- Branching + review: the same human-in-the-loop shape Actions already
-- use (a `write`-tier request, an `approve`-tier decision — role
-- separation, not a same-URN check, same as `action_approval`), applied
-- to ontology changes instead of data writes. A branch is a named
-- pointer at a specific `object_type_version` row; review approval
-- merges by calling the *existing* `publish_object_type_version`
-- unchanged, so every validation/event it already does still applies.
CREATE TABLE IF NOT EXISTS ontology_branch (
    id BIGSERIAL PRIMARY KEY,
    object_type_urn TEXT NOT NULL REFERENCES object_type(urn),
    tenant_id TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    version INT NOT NULL,
    created_by_urn TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (object_type_urn, branch_name)
);

CREATE TABLE IF NOT EXISTS ontology_review (
    id BIGSERIAL PRIMARY KEY,
    branch_id BIGINT NOT NULL REFERENCES ontology_branch(id),
    tenant_id TEXT NOT NULL,
    reviewer_urn TEXT NOT NULL,
    decision TEXT NOT NULL,
    note TEXT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Generalizes branching beyond ObjectType to the 4 other governed
-- registries (RelationType, ValueType, SharedPropertyType, ActionType —
-- matching Foundry's own branching scope minus Type groups/Rule sets).
-- Existing ObjectType branch rows are untouched: `resource_type`
-- defaults to 'object_type' and `resource_name`/`proposed_definition`
-- stay NULL for them. The generic branching code
-- (`ontology/resource_branching.py`) only ever operates on the other 4
-- resource_type values — a disjoint set from 'object_type' — so the two
-- code paths never collide on the same table despite sharing it.
ALTER TABLE ontology_branch ALTER COLUMN object_type_urn DROP NOT NULL;
ALTER TABLE ontology_branch ALTER COLUMN version DROP NOT NULL;
ALTER TABLE ontology_branch ADD COLUMN IF NOT EXISTS resource_type TEXT NOT NULL DEFAULT 'object_type';
ALTER TABLE ontology_branch ADD COLUMN IF NOT EXISTS resource_name TEXT;
ALTER TABLE ontology_branch ADD COLUMN IF NOT EXISTS proposed_definition JSONB;
CREATE UNIQUE INDEX IF NOT EXISTS ontology_branch_resource_unique
    ON ontology_branch (tenant_id, resource_type, resource_name, branch_name)
    WHERE resource_name IS NOT NULL;

-- Org/Space/Project hierarchy: an ObjectType can optionally
-- scope down to a project, one tier narrower than its default workspace
-- scope. Validated against Identity's real project registry at publish
-- time (`_validate_project_scope`, an HTTP call — Knowledge never reads
-- Identity's database directly, same cross-service boundary this build
-- keeps everywhere else); the SpiceDB `parent_project` relationship
-- itself is written by `main.py` after a successful publish (this module
-- only holds Postgres state, main.py already owns every other
-- authz-relationship write in this service).
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS project_urn TEXT;
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS project_urn TEXT;

-- Markings: a genuinely separate mechanism from `classification`
-- above, not a replacement — named, admin-created labels (e.g. "PII",
-- "Export-Controlled") that compose (a resource can carry several; a
-- principal must hold every one, checked via the SpiceDB `marking` resource's
-- `hold` permission, `main.py`'s `_authorize_markings`). `object_type`/
-- `object_type_version` carry ObjectType-wide markings the same versioned
-- way `implements`/`derived_properties` do (validated at publish time,
-- `_validate_markings`); `instance_marking` is the separate, unversioned
-- per-instance case the plan calls out as the other attachment point —
-- a marking set directly on one object (e.g. this one Customer row, not
-- every Customer), enforced at the same read choke point (`_resolve_one`/
-- `_resolve_many` in `main.py`) rather than a second one.
CREATE TABLE IF NOT EXISTS marking (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS instance_marking (
    object_type_urn TEXT NOT NULL REFERENCES object_type(urn),
    tenant_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    markings JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (object_type_urn, tenant_id, instance_id)
);

ALTER TABLE object_type ADD COLUMN IF NOT EXISTS markings JSONB NOT NULL DEFAULT '[]';
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS markings JSONB NOT NULL DEFAULT '[]';

-- Property formatting: {"lifetimeValue": {"kind": "currency", "currency": "USD"}}
-- or {"segment": {"kind": "badge", "colors": {"enterprise": "primary", ...}}}.
-- Presentation metadata only — resolver.py/serving_store.py keep serving
-- raw values unchanged, exactly like classification/markings never rewrite
-- a value in place. Validated at publish time (`_validate_property_formats`
-- in publishing.py: known property, known `kind`, well-formed rule), same
-- versioned governance treatment as `derived_properties` above — not
-- boot-seeded here for the same reason that field isn't (see module
-- docstring): a governed field must come from a real propose/publish call,
-- not be silently reset on every restart.
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS property_formats JSONB NOT NULL DEFAULT '{}';
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS property_formats JSONB NOT NULL DEFAULT '{}';

-- Conditional formatting: {"lifetimeValue": [{"condition": {"type":
-- "number-range", "max": 0}, "style": {"color": "danger"}}, ...]}.
-- Deliberately a sibling field to `property_formats` above, not a `style`
-- key folded into a `PropertyFormatRule` — genuinely separate concerns
-- (this styles a value already rendered; property_formats controls the
-- value's own textual form). Same versioned governance treatment,
-- validated at publish time (`_validate_conditional_formats`).
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS conditional_formats JSONB NOT NULL DEFAULT '{}';
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS conditional_formats JSONB NOT NULL DEFAULT '{}';

-- Declared per-column classification for a self-serve ObjectType
-- (`create_object_type`) — {"email": "confidential", "id": "public"},
-- keyed by *source column* the same way `object_type_property` already
-- is (see that table's own comment). The six boot-seeded types get this
-- from a hand-written Python constant (`CUSTOMERS_COLUMN_CLASSIFICATION`
-- etc., `catalog.py`) reviewed once by whoever wrote the connector; a
-- self-serve type has no such constant to fall back on, so the admin's
-- choice at creation time has to be persisted somewhere `catalog.py`'s
-- sync consumer can re-read on *every* sync, not just the first —
-- without this, the very re-sync `create_object_type`'s caller triggers
-- to materialize the type would immediately overwrite it back to the
-- "everything internal" default. Not part of the versioned
-- propose/publish lifecycle (unlike `property_formats` above) —
-- narrower, deliberately: see `create_object_type`'s own docstring.
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS column_classification JSONB NOT NULL DEFAULT '{}';

-- Value Types: reusable, named *data* types (string/integer/double/
-- boolean/date/timestamp, optionally format-constrained by regex) — a
-- genuinely separate concern from `property_formats` above (display
-- formatting only). Registered once (`value_types.py`), referenced by
-- name from two places: a typed property in `property_types` below, and
-- a declarative Action's parameter (`action_types.py`).
CREATE TABLE IF NOT EXISTS value_type (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    base_type TEXT NOT NULL,
    format_regex TEXT,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

-- Richer constraints (enum/range/rid/uuid) beyond the single format_regex
-- above — see value_types.py's module docstring for why exactly these
-- four and not Foundry's full eight.
ALTER TABLE value_type ADD COLUMN IF NOT EXISTS constraints JSONB NOT NULL DEFAULT '[]';

-- Shared Property Types: a canonical, reusable *property* definition
-- (api_name + display_name + description) wrapping a Value Type for
-- its data shape — see shared_property_types.py's module docstring for
-- the distinction from a bare Value Type. Same upsert-registry shape
-- as `value_type` above.
CREATE TABLE IF NOT EXISTS shared_property_type (
    tenant_id TEXT NOT NULL,
    api_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    value_type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, api_name)
);

-- Typed properties: {"email": {"kind": "value_type", "value_type": "Email"}}
-- (a single typed leaf), {"email": {"kind": "shared_property_type",
-- "shared_property_type": "email"}} (a leaf naming a canonical, reused
-- property definition instead of a bare Value Type), {"address":
-- {"kind": "struct", "properties": {"street": {...}, "city": {...}}}}
-- (one level of nesting only — scope decision, stated plainly), or
-- {"tags": {"kind": "array", "element": {...}}} (a list of a single
-- element type). Validated at publish time (`_validate_property_types`
-- in publishing.py: known property, real `value_type`/
-- `shared_property_type` reference, well-formed struct/array), same
-- versioned governance treatment as `property_formats`/
-- `derived_properties`. A `struct`/`array`-typed property's underlying
-- source column holds JSON text, parsed into a real nested dict/list at
-- read time (`core.py`) — not a schema that only exists on paper.
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS property_types JSONB NOT NULL DEFAULT '{}';
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS property_types JSONB NOT NULL DEFAULT '{}';

-- Foundry-parity+ ObjectType presentation / identity metadata (versioned).
-- `lifecycle_status` is experimental|active|deprecated — distinct from
-- `object_type_version.status` (draft|published).
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS primary_key TEXT NOT NULL DEFAULT 'id';
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS title_key TEXT;
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS plural_display_name TEXT NOT NULL DEFAULT '';
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'experimental';
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'normal';
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS icon TEXT;
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS primary_key TEXT NOT NULL DEFAULT 'id';
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS title_key TEXT;
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS plural_display_name TEXT NOT NULL DEFAULT '';
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'experimental';
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'normal';
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS icon TEXT;

-- Link Type storage kinds beyond FK (Foundry join-table + object-backed).
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS storage_kind TEXT NOT NULL DEFAULT 'foreign_key';
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS join_dataset_urn TEXT;
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS join_source_column TEXT;
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS join_target_column TEXT;
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS mid_object_type_urn TEXT;
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS mid_source_property TEXT;
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS mid_target_property TEXT;

-- Object Sets: filtered collections of instances (Knowledge-owned artefact).
CREATE TABLE IF NOT EXISTS object_set (
    urn TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    object_type_urn TEXT NOT NULL,
    definition JSONB NOT NULL DEFAULT '{"all":[]}',
    lifecycle_status TEXT NOT NULL DEFAULT 'experimental',
    visibility TEXT NOT NULL DEFAULT 'normal',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

-- Action Types: the no-code counterpart to `actions.py`'s hardcoded
-- `ACTION_DEFINITIONS` + `register_apply_function`. A simple upsert
-- registry (like `interface_type`/`marking` above), not versioned —
-- see `ontology/action_types.py`'s own module docstring for the full
-- shape of `parameters`/`edits`/`submission_criteria`. Applied at
-- request/apply time by `actions.py`, which also owns the generic
-- `object_instance_edit` overlay table these Actions write to.
CREATE TABLE IF NOT EXISTS action_type (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    target_object_type TEXT NOT NULL,
    required_permission TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    description TEXT NOT NULL,
    parameters JSONB NOT NULL DEFAULT '[]',
    edits JSONB NOT NULL DEFAULT '[]',
    submission_criteria JSONB NOT NULL DEFAULT '[]',
    function_side_effect TEXT,
    -- Writeback: names a `write_target` dataset registered in
    -- Connectivity (`POST /write-targets`), by its `dataset_name`. When
    -- set, an approved high-risk invocation of this Action Type also
    -- mirrors its applied `edits` back to the source system, via the
    -- same saga (Knowledge -> event bus -> Automation -> Connectivity ->
    -- compensate-on-failure) `Customer.closeAccount` already uses —
    -- generalized, not replaced; see `actions.py`'s
    -- `compensate_from_workflow_engine` and `services/automation/app/
    -- workflow.py`. `NULL` (the default) means "local-only", exactly
    -- today's declarative-Action behavior, unaffected.
    writeback_dataset TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

-- additive migration for databases seeded before this column existed
ALTER TABLE action_type ADD COLUMN IF NOT EXISTS writeback_dataset TEXT;

-- Actions on interfaces: an Action Type may target an Interface instead
-- of one ObjectType, becoming invocable against any instance of any
-- ObjectType that currently `implements` it — same generalization
-- Foundry's own "interface action rules" apply, restricted the same way
-- (see `declarative.request_generic_action`): only the interface's own
-- `required_properties` may be edited, never a type-specific one.
-- Exactly one of `target_object_type`/`target_interface` is set, checked
-- in `action_types.create_action_type`, not here.
ALTER TABLE action_type ALTER COLUMN target_object_type DROP NOT NULL;
ALTER TABLE action_type ADD COLUMN IF NOT EXISTS target_interface TEXT;

-- Function-backed Actions: an Action Type may declare `edit_function`
-- instead of a static `edits` list — the named Function plugin's return
-- value BECOMES the applied edits (dynamic logic), as opposed to
-- `function_side_effect` (fire-and-forget, result discarded). Exactly
-- one of `edits`/`edit_function` is set, checked in
-- `action_types.create_action_type`, not here.
ALTER TABLE action_type ADD COLUMN IF NOT EXISTS edit_function TEXT;

-- Configure/Sections: an ordered list of {name, parameter_names} groupings
-- an Action Type may optionally declare, purely a display concern for the
-- invocation form (Foundry's "Sections") — never affects what gets
-- submitted, validated, or applied. A parameter not referenced by any
-- section renders ungrouped, same as before this column existed.
-- Structural validation (names exist, no parameter in two sections) lives
-- in `action_types.create_action_type`, not here.
ALTER TABLE action_type ADD COLUMN IF NOT EXISTS sections JSONB NOT NULL DEFAULT '[]';
"""


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
            INSERT INTO relation_type (urn, tenant_id, name, source_object_type_urn, target_object_type_urn, source_property, target_property, cardinality)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (urn) DO UPDATE SET target_property = EXCLUDED.target_property
            """,
            build_urn(tenant_id, workspace_id, "relation-type", relation["name"]),
            tenant_id,
            relation["name"],
            object_type_urn(tenant_id, workspace_id, relation["source_object_type"]),
            object_type_urn(tenant_id, workspace_id, relation["target_object_type"]),
            relation["source_property"],
            relation["target_property"],
            relation["cardinality"],
        )

    # Seed title keys for demo types (primary_key defaults to id via DDL).
    for name, title_key, plural, icon in (
        ("Customer", "name", "Customers", "people"),
        ("Order", "id", "Orders", "shopping-cart"),
        ("SupportTicket", "subject", "Support tickets", "chat"),
        ("ProductReview", "id", "Product reviews", "star"),
        ("Supplier", "name", "Suppliers", "office"),
        ("InventoryLevel", "id", "Inventory levels", "box"),
    ):
        await pool.execute(
            """
            UPDATE object_type SET
                title_key = COALESCE(title_key, $2),
                plural_display_name = CASE WHEN plural_display_name = '' THEN $3 ELSE plural_display_name END,
                icon = COALESCE(icon, $4),
                lifecycle_status = CASE WHEN lifecycle_status = 'experimental' THEN 'active' ELSE lifecycle_status END
            WHERE tenant_id = $1 AND name = $5 AND version = 1
            """,
            tenant_id, title_key, plural, icon, name,
        )


async def create_object_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    source_dataset_urn: str,
    property_mapping: dict,
    description: str,
    column_classification: Optional[dict[str, str]] = None,
    primary_key: str = "id",
    title_key: Optional[str] = None,
    plural_display_name: str = "",
    lifecycle_status: str = "experimental",
    visibility: str = "normal",
    icon: Optional[str] = None,
) -> dict:
    """The self-serve path: turn an already-synced Dataset into a
    browsable ObjectType by name and column mapping alone — no code,
    same as the no-code connector that got the data in in the first
    place. Reuses `_upsert_object_type` (same insert every boot-seeded
    type goes through) rather than a parallel code path, so a self-serve
    type is a real `object_type` row indistinguishable from a seeded one
    at read time. Creation itself is existence + mapping; branching,
    interfaces, markings, and the rest attach afterward via the normal
    versioning endpoints, same as a seeded type.

    `column_classification` (source column -> "public"/"internal"/
    "confidential"/"restricted") is the admin's one chance to say which
    columns are sensitive — the six boot-seeded types get this from a
    hand-reviewed Python constant instead; a self-serve type has none,
    so skipping this arg means every column defaults to internal
    (`catalog.py`'s dynamic-dispatch branch), not automatically
    downgraded to public. Persisted directly on `object_type` (not
    versioned) so `catalog.py`'s sync consumer can re-read the same
    declared values on every subsequent sync, not just this first write.

    Caller-responsible: writing the SpiceDB `parent_workspace`
    relationship this type needs to ever be readable at all — this
    function only owns the Postgres row, the same split every other
    ontology-governance write in this build already keeps (`main.py`/
    the relevant router owns authz-relationship writes, this package
    owns state).
    """
    validate_ot_metadata(
        property_mapping=property_mapping,
        primary_key=primary_key,
        title_key=title_key,
        lifecycle_status=lifecycle_status,
        visibility=visibility,
    )
    urn = object_type_urn(tenant_id, workspace_id, name)
    if await get_object_type(pool, urn) is not None:
        raise ValueError(f"an ObjectType named {name!r} already exists")
    await _upsert_object_type(pool, urn, tenant_id, name, source_dataset_urn, property_mapping, description)
    await pool.execute(
        """
        UPDATE object_type SET
            primary_key = $1, title_key = $2, plural_display_name = $3,
            lifecycle_status = $4, visibility = $5, icon = $6
        WHERE urn = $7
        """,
        primary_key, title_key, plural_display_name or "", lifecycle_status, visibility, icon, urn,
    )

    declared = column_classification or {}
    if declared:
        overall = most_restrictive(*(Classification(value) for value in declared.values()))
        await pool.execute(
            "UPDATE object_type SET column_classification = $1::jsonb, classification = $2 WHERE urn = $3",
            json.dumps(declared), overall.value, urn,
        )
    return await get_object_type(pool, urn)


async def get_object_type_by_dataset(pool: asyncpg.Pool, tenant_id: str, source_dataset_urn: str) -> dict | None:
    row = await pool.fetchrow(
        "SELECT * FROM object_type WHERE tenant_id = $1 AND source_dataset_urn = $2", tenant_id, source_dataset_urn
    )
    return _parse_jsonb_keys(row, _OT_JSONB_KEYS) if row is not None else None


async def get_object_type(pool: asyncpg.Pool, urn: str) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM object_type WHERE urn = $1", urn)
    return _parse_jsonb_keys(row, _OT_JSONB_KEYS) if row is not None else None


def _parse_version_row(row: asyncpg.Record) -> dict:
    return _parse_jsonb_keys(row, _OTV_JSONB_KEYS)


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


async def list_object_types(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    """A real, previously-missing gap: `RelationType` always had
    `list_relation_types`/`GET /relation-types`, but `ObjectType` never
    got the equivalent — every existing caller already knew the six
    hardcoded type names. A genuine Object Explorer UI needs to discover
    them, not hardcode the same list a second time client-side.

    Parses every JSONB column, not just `property_mapping` — leaving
    `implements`/`derived_properties`/`markings`/`property_formats`/
    `property_types`/`column_classification` as raw JSON *strings* would
    make this endpoint diverge from `get_object_type` (`GET
    /ontology/{name}`, detail), which already parses all of them. An
    OSDK generator walking the list endpoint needs to trust it the same
    way any other caller trusts the detail endpoint.
    """
    rows = await pool.fetch("SELECT * FROM object_type WHERE tenant_id = $1 ORDER BY name", tenant_id)
    return [_parse_jsonb_keys(row, _OT_JSONB_KEYS) for row in rows]


async def delete_object_type(pool: asyncpg.Pool, urn: str) -> None:
    """Compensating delete after a failed SpiceDB `parent_workspace`
    write on create — rolls back the Postgres row so we never leave an
    ObjectType that exists in PG but is unreadable via ReBAC.

    Refuses if any `object_type_version` (or property/marking/branch)
    rows already reference the type: those mean lifecycle has started
    and a blind delete would orphan history. Brand-new self-serve
    creates have none of those yet.
    """
    async with pool.acquire() as conn, conn.transaction():
        version_count = await conn.fetchval(
            "SELECT COUNT(*) FROM object_type_version WHERE object_type_urn = $1", urn
        )
        if version_count:
            raise ValueError(
                f"refusing to delete {urn}: {version_count} object_type_version row(s) already exist"
            )
        branch_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ontology_branch WHERE object_type_urn = $1", urn
        )
        if branch_count:
            raise ValueError(f"refusing to delete {urn}: {branch_count} ontology_branch row(s) already exist")
        await conn.execute("DELETE FROM object_type_property WHERE object_type_urn = $1", urn)
        await conn.execute("DELETE FROM instance_marking WHERE object_type_urn = $1", urn)
        result = await conn.execute("DELETE FROM object_type WHERE urn = $1", urn)
        if result == "DELETE 0":
            raise ValueError(f"unknown ObjectType: {urn}")
