-- Automation schema baseline. Idempotent so a database that already
-- ran ensure_schema() at boot accepts this file as a no-op.

CREATE TABLE IF NOT EXISTS workflow_execution (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    approval_id BIGINT NOT NULL,
    action_name TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --- audit_event (automation copy of holon_common.audit_store) ---
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

-- --- event_outbox (automation copy of holon_common.outbox) ---
CREATE TABLE IF NOT EXISTS event_outbox (
    id BIGSERIAL PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    envelope JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ
);
