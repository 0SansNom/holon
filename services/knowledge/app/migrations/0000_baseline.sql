-- Knowledge schema baseline (pre-0001). CREATE/ALTER IF NOT EXISTS so this
-- is a no-op on databases that already ran ensure_schema(). Fresh installs
-- get tables here; 0001–0004 then apply the non-additive follow-ups.
--
-- lineage_edge has no UNIQUE here: 0001 installs lineage_edge_full_key.

-- --- catalog ---
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

-- --- lineage (no unique — 0001 adds lineage_edge_full_key) ---
CREATE TABLE IF NOT EXISTS lineage_edge (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    source_urn TEXT NOT NULL,
    target_urn TEXT NOT NULL,
    relation TEXT NOT NULL,
    source_column TEXT NOT NULL DEFAULT '',
    target_property TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- additive migrations for databases seeded before column-level lineage existed
ALTER TABLE lineage_edge ADD COLUMN IF NOT EXISTS source_column TEXT NOT NULL DEFAULT '';
ALTER TABLE lineage_edge ADD COLUMN IF NOT EXISTS target_property TEXT NOT NULL DEFAULT '';

-- --- ontology / object_types ---
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

-- Markings: separate from `classification`. Named labels live in a
-- category (CONJUNCTIVE = hold all applied in that category; DISJUNCTIVE =
-- hold at least one). SpiceDB `marking` stays flat (`hold`); names remain
-- unique per tenant. Migration 0003 adds id/category_id + Default category
-- for existing rows; ensure_schema creates the category table and columns.
CREATE TABLE IF NOT EXISTS marking_category (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category_type TEXT NOT NULL CHECK (category_type IN ('CONJUNCTIVE', 'DISJUNCTIVE')),
    marking_type TEXT NOT NULL DEFAULT 'MANDATORY' CHECK (marking_type IN ('MANDATORY')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS marking (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

ALTER TABLE marking ADD COLUMN IF NOT EXISTS id UUID;
ALTER TABLE marking ADD COLUMN IF NOT EXISTS category_id UUID;
CREATE INDEX IF NOT EXISTS marking_category_tenant_idx ON marking_category (tenant_id);
CREATE INDEX IF NOT EXISTS marking_tenant_category_idx ON marking (tenant_id, category_id);

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
-- auto-filled here for the same reason that field isn't (see module
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
-- is (see that table's own comment). Example connector plugins ship with
-- hand-written Python constants (`CUSTOMERS_COLUMN_CLASSIFICATION`
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

-- Foundry Value Type metadata + versioning: api/display names, example
-- preview, integer version (bumped when constraints/format change), and
-- lifecycle for deprecate-without-delete.
ALTER TABLE value_type ADD COLUMN IF NOT EXISTS api_name TEXT NOT NULL DEFAULT '';
ALTER TABLE value_type ADD COLUMN IF NOT EXISTS display_name TEXT NOT NULL DEFAULT '';
ALTER TABLE value_type ADD COLUMN IF NOT EXISTS example_value TEXT;
ALTER TABLE value_type ADD COLUMN IF NOT EXISTS version INT NOT NULL DEFAULT 1;
ALTER TABLE value_type ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'experimental';
ALTER TABLE value_type ADD COLUMN IF NOT EXISTS deprecation_reason TEXT;
ALTER TABLE value_type ADD COLUMN IF NOT EXISTS deprecation_deadline DATE;
ALTER TABLE value_type ADD COLUMN IF NOT EXISTS replacement_urn TEXT;
-- Foundry regex may match a substring; default full = re.fullmatch.
ALTER TABLE value_type ADD COLUMN IF NOT EXISTS format_regex_match TEXT NOT NULL DEFAULT 'full';
-- Optional project import (SpiceDB parent_project), same as SPT / RelationType.
ALTER TABLE value_type ADD COLUMN IF NOT EXISTS project_urn TEXT;

CREATE TABLE IF NOT EXISTS value_type_revision (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    version INT NOT NULL,
    base_type TEXT NOT NULL,
    format_regex TEXT,
    constraints JSONB NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT '',
    api_name TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    example_value TEXT,
    lifecycle_status TEXT NOT NULL DEFAULT 'experimental',
    format_regex_match TEXT NOT NULL DEFAULT 'full',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name, version)
);
ALTER TABLE value_type_revision ADD COLUMN IF NOT EXISTS format_regex_match TEXT NOT NULL DEFAULT 'full';

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
-- Struct-typed Shared Property Types (Foundry parity): either wrap a
-- Value Type (`value_type` set) or carry a one-level struct field map
-- (`struct_properties` set, `value_type` null). Additive migration —
-- existing VT-wrapped rows stay valid.
ALTER TABLE shared_property_type ADD COLUMN IF NOT EXISTS struct_properties JSONB;
ALTER TABLE shared_property_type ALTER COLUMN value_type DROP NOT NULL;
-- Foundry-parity shared property metadata (inherited by local properties
-- that reference this SPT when the local entry doesn't override).
ALTER TABLE shared_property_type ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'normal';
ALTER TABLE shared_property_type ADD COLUMN IF NOT EXISTS render_hints JSONB NOT NULL DEFAULT '["searchable"]';
ALTER TABLE shared_property_type ADD COLUMN IF NOT EXISTS type_classes JSONB NOT NULL DEFAULT '[]';
ALTER TABLE shared_property_type ADD COLUMN IF NOT EXISTS property_format JSONB;
-- Foundry aliases: alternate search terms for the shared property.
ALTER TABLE shared_property_type ADD COLUMN IF NOT EXISTS aliases JSONB NOT NULL DEFAULT '[]';
-- Optional project scope (additive ReBAC path via SpiceDB parent_project).
ALTER TABLE shared_property_type ADD COLUMN IF NOT EXISTS project_urn TEXT;

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
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS deprecation_reason TEXT;
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS deprecation_deadline DATE;
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS replacement_urn TEXT;
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS primary_key TEXT NOT NULL DEFAULT 'id';
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS title_key TEXT;
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS plural_display_name TEXT NOT NULL DEFAULT '';
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'experimental';
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'normal';
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS icon TEXT;
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS deprecation_reason TEXT;
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS deprecation_deadline DATE;
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS replacement_urn TEXT;

-- Link Type storage kinds beyond FK (Foundry join-table + object-backed).
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS storage_kind TEXT NOT NULL DEFAULT 'foreign_key';
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS join_dataset_urn TEXT;
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS join_source_column TEXT;
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS join_target_column TEXT;
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS mid_object_type_urn TEXT;
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS mid_source_property TEXT;
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS mid_target_property TEXT;

-- Foundry Link Type metadata: per-side display/API/visibility + type-level status/classes.
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS source_display_name TEXT NOT NULL DEFAULT '';
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS source_plural_display_name TEXT NOT NULL DEFAULT '';
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS source_api_name TEXT NOT NULL DEFAULT '';
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS source_visibility TEXT NOT NULL DEFAULT 'normal';
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS target_display_name TEXT NOT NULL DEFAULT '';
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS target_plural_display_name TEXT NOT NULL DEFAULT '';
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS target_api_name TEXT NOT NULL DEFAULT '';
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS target_visibility TEXT NOT NULL DEFAULT 'normal';
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'experimental';
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS type_classes JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS project_urn TEXT;
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS deprecation_reason TEXT;
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS deprecation_deadline DATE;
ALTER TABLE relation_type ADD COLUMN IF NOT EXISTS replacement_urn TEXT;

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
    -- mirrors its applied `edits` back to the source system, via a
    -- saga (Knowledge -> event bus -> Automation -> Connectivity ->
    -- compensate-on-failure); see `actions.py`'s
    -- `compensate_from_workflow_engine` and `services/automation/app/
    -- workflow.py`. `NULL` (the default) means "local-only", exactly
    -- today's declarative-Action behavior, unaffected.
    writeback_dataset TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

-- additive migration for databases seeded before this column existed
ALTER TABLE action_type ADD COLUMN IF NOT EXISTS writeback_dataset TEXT;

-- Foundry Type classes on Action Types (e.g. hubble-oe:hide-action).
ALTER TABLE action_type ADD COLUMN IF NOT EXISTS type_classes JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE action_type ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'experimental';
ALTER TABLE action_type ADD COLUMN IF NOT EXISTS deprecation_reason TEXT;
ALTER TABLE action_type ADD COLUMN IF NOT EXISTS deprecation_deadline DATE;
ALTER TABLE action_type ADD COLUMN IF NOT EXISTS replacement_urn TEXT;
ALTER TABLE interface_type ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'experimental';
ALTER TABLE interface_type ADD COLUMN IF NOT EXISTS deprecation_reason TEXT;
ALTER TABLE interface_type ADD COLUMN IF NOT EXISTS deprecation_deadline DATE;
ALTER TABLE interface_type ADD COLUMN IF NOT EXISTS replacement_urn TEXT;
-- P1a: optional typed bindings for required_properties (value_type /
-- shared_property_type only — same leaf kinds OT property_types uses).
ALTER TABLE interface_type ADD COLUMN IF NOT EXISTS property_types JSONB NOT NULL DEFAULT '{}'::jsonb;
-- P1b: abstract link constraints fulfilled by concrete RelationTypes at implement.
ALTER TABLE interface_type ADD COLUMN IF NOT EXISTS link_constraints JSONB NOT NULL DEFAULT '[]'::jsonb;
-- P1c: Foundry-style interface inheritance (child extends parents).
ALTER TABLE interface_type ADD COLUMN IF NOT EXISTS parent_interfaces JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS link_constraint_bindings JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS link_constraint_bindings JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE object_type ADD COLUMN IF NOT EXISTS interface_property_bindings JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE object_type_version ADD COLUMN IF NOT EXISTS interface_property_bindings JSONB NOT NULL DEFAULT '{}'::jsonb;


-- Actions on interfaces: an Action Type may target an Interface instead
-- of one ObjectType, becoming invocable against any instance of any
-- ObjectType that currently `implements` it — same generalization
-- Foundry's own "interface action rules" apply, restricted the same way
-- (see `declarative.request_generic_action`): only the interface's own
-- `required_properties` may be edited, never a type-specific one.
-- Exactly one of `target_object_type`/`target_interface` is set, checked
-- in `action_types.create_action_type`, not here.
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

-- P2c: optional HTTP webhook URL fired best-effort after request/apply/expire
-- (Slack incoming webhook, Zapier, etc.). NULL = no outbound notify.
ALTER TABLE action_type ADD COLUMN IF NOT EXISTS notify_webhook TEXT;

-- --- link_overlays ---
CREATE TABLE IF NOT EXISTS relation_link_overlay (
    tenant_id TEXT NOT NULL,
    relation_urn TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    op TEXT NOT NULL CHECK (op IN ('add', 'delete')),
    mid_id TEXT,
    set_by_urn TEXT NOT NULL,
    set_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, relation_urn, source_id, target_id)
);
CREATE INDEX IF NOT EXISTS relation_link_overlay_relation_idx
    ON relation_link_overlay (tenant_id, relation_urn);

-- --- actions ---
CREATE TABLE IF NOT EXISTS action_invocation (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    action_name TEXT NOT NULL,
    instance_urn TEXT NOT NULL,
    actor_urn TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    invoked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Undo/revert support — additive. `edits` is `{property: newValue}`;
-- `prior_values` is `{property: {"existed": bool, "value": ...}}`.
ALTER TABLE action_invocation ADD COLUMN IF NOT EXISTS edits JSONB;
ALTER TABLE action_invocation ADD COLUMN IF NOT EXISTS prior_values JSONB;
ALTER TABLE action_invocation ADD COLUMN IF NOT EXISTS reverted_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS action_approval (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    action_name TEXT NOT NULL,
    instance_urn TEXT NOT NULL,
    requested_by_urn TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '24 hours'),
    decided_by_urn TEXT,
    decided_at TIMESTAMPTZ,
    decision_note TEXT
);

ALTER TABLE action_approval ADD COLUMN IF NOT EXISTS parameters JSONB NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS saga_execution (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    approval_id BIGINT NOT NULL,
    action_name TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS object_instance_edit (
    tenant_id TEXT NOT NULL,
    object_type TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    property_name TEXT NOT NULL,
    property_value JSONB NOT NULL,
    set_by_action_urn TEXT NOT NULL,
    set_by_urn TEXT NOT NULL,
    set_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, object_type, instance_id, property_name)
);

-- --- action tombstones ---
CREATE TABLE IF NOT EXISTS object_instance_tombstone (
    tenant_id TEXT NOT NULL,
    object_type TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    prior_data JSONB,
    set_by_action_urn TEXT NOT NULL,
    set_by_urn TEXT NOT NULL,
    set_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, object_type, instance_id)
);

-- --- serving_store ---
CREATE TABLE IF NOT EXISTS object_instance (
    object_type TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    data JSONB NOT NULL,
    source_snapshot_id BIGINT NOT NULL,
    materialized_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (object_type, tenant_id, instance_id)
);

-- Bi-temporal instance history — transaction-time only. Append-only:
-- unlike `object_instance` above, rows here are never updated or deleted,
-- so "what did the system believe at time T" stays answerable indefinitely.
CREATE TABLE IF NOT EXISTS object_instance_history (
    object_type TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    data JSONB NOT NULL,
    source_snapshot_id BIGINT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Soft-delete markers written by declarative Action rules (`delete_object`).
-- Reads hide tombstoned ids even when the underlying row still exists.
CREATE TABLE IF NOT EXISTS object_instance_tombstone (
    tenant_id TEXT NOT NULL,
    object_type TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    prior_data JSONB,
    set_by_action_urn TEXT NOT NULL,
    set_by_urn TEXT NOT NULL,
    set_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, object_type, instance_id)
);

CREATE INDEX IF NOT EXISTS object_instance_lookup
    ON object_instance (object_type, tenant_id, instance_id);
CREATE INDEX IF NOT EXISTS object_instance_history_as_of
    ON object_instance_history (object_type, tenant_id, instance_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS object_instance_tombstone_lookup
    ON object_instance_tombstone (tenant_id, object_type, instance_id);

-- --- execution ---
CREATE TABLE IF NOT EXISTS execution_run (
    plan_hash TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    plan JSONB NOT NULL,
    result JSONB NOT NULL,
    row_count INTEGER NOT NULL,
    executed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    cache_hits INTEGER NOT NULL DEFAULT 0
);

-- --- plugin_registration ---
CREATE TABLE IF NOT EXISTS plugin_registration (
    name TEXT PRIMARY KEY,
    plugin_type TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest JSONB NOT NULL,
    checksum TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --- glossary ---
CREATE TABLE IF NOT EXISTS business_glossary (
    tenant_id TEXT NOT NULL,
    term TEXT NOT NULL,
    definition TEXT NOT NULL,
    synonyms TEXT[] NOT NULL DEFAULT '{}',
    related_object_type_urn TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, term)
);

-- --- query_log ---
CREATE TABLE IF NOT EXISTS query_log (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    query_text TEXT NOT NULL,
    result_count INTEGER NOT NULL,
    executed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --- audit_event (knowledge copy of holon_common.audit_store) ---
CREATE TABLE IF NOT EXISTS audit_event (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    category TEXT NOT NULL,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL,
    actor_urn TEXT,
    actor_type TEXT,
    resource_type TEXT,
    resource_urn TEXT,
    permission TEXT,
    reason TEXT,
    trace_id TEXT,
    request_id TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS audit_event_tenant_occurred_idx
    ON audit_event (tenant_id, occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS audit_event_tenant_category_idx
    ON audit_event (tenant_id, category, occurred_at DESC);
CREATE INDEX IF NOT EXISTS audit_event_tenant_actor_idx
    ON audit_event (tenant_id, actor_urn, occurred_at DESC);

-- --- event_outbox (knowledge copy of holon_common.outbox) ---
CREATE TABLE IF NOT EXISTS event_outbox (
    id BIGSERIAL PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    envelope JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ
);
