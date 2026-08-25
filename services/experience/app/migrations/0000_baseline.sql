-- Experience schema baseline. CREATE/ALTER IF NOT EXISTS so this is a
-- no-op on databases that already ran ensure_schema(). Fresh installs
-- get tables here.

-- --- application builder ---
CREATE TABLE IF NOT EXISTS application (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    version INT NOT NULL,
    definition JSONB NOT NULL,
    dependencies JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    promoted_at TIMESTAMPTZ,
    UNIQUE (tenant_id, name, version)
);

ALTER TABLE application ADD COLUMN IF NOT EXISTS urn TEXT;
ALTER TABLE application ADD COLUMN IF NOT EXISTS project_urn TEXT;

CREATE TABLE IF NOT EXISTS agent_app_session (
    session_urn TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    application_name TEXT NOT NULL,
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --- collections ---
CREATE TABLE IF NOT EXISTS collection (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS collection_member (
    collection_id BIGINT NOT NULL REFERENCES collection(id) ON DELETE CASCADE,
    resource_urn TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    added_by_urn TEXT NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (collection_id, resource_urn)
);

-- --- resource tags / featured ---
CREATE TABLE IF NOT EXISTS resource_tag (
    resource_urn TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    tags JSONB NOT NULL DEFAULT '[]',
    featured BOOLEAN NOT NULL DEFAULT false,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by_urn TEXT NOT NULL,
    PRIMARY KEY (resource_urn, tenant_id)
);

-- --- project pins ---
CREATE TABLE IF NOT EXISTS project_pin (
    project_urn TEXT NOT NULL,
    resource_urn TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    pinned_by_urn TEXT NOT NULL,
    pinned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_urn, resource_urn)
);

-- --- plugin_registration (experience copy of holon_common.plugin) ---
CREATE TABLE IF NOT EXISTS plugin_registration (
    name TEXT PRIMARY KEY,
    plugin_type TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest JSONB NOT NULL,
    checksum TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --- audit_event (experience copy of holon_common.audit_store) ---
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
