-- Intelligence schema baseline. CREATE/ALTER IF NOT EXISTS so this is a
-- no-op on databases that already ran ensure_schema(). Fresh installs get
-- tables here; schema lives only in migrations going forward.

-- --- runtime flags (authz backfill, etc.) ---
CREATE TABLE IF NOT EXISTS intelligence_runtime (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --- agent sessions / turns ---
CREATE TABLE IF NOT EXISTS agent_session (
    urn TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    agent_urn TEXT NOT NULL,
    on_behalf_of TEXT,
    budget JSONB NOT NULL,
    consumed JSONB NOT NULL DEFAULT '{"iterations": 0, "tool_calls": 0, "tokens": 0}',
    status TEXT NOT NULL DEFAULT 'running',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    causation_id TEXT,
    causation_depth INT NOT NULL DEFAULT 0,
    chain_trigger BOOLEAN NOT NULL DEFAULT FALSE,
    max_chain_depth INT NOT NULL DEFAULT 10,
    allowed_tools JSONB,
    system_prompt TEXT
);

CREATE TABLE IF NOT EXISTS agent_turn (
    id BIGSERIAL PRIMARY KEY,
    session_urn TEXT NOT NULL REFERENCES agent_session(urn),
    role TEXT NOT NULL,
    content JSONB NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Additive columns for DBs that already had the older ensure_schema shape.
ALTER TABLE agent_session ADD COLUMN IF NOT EXISTS causation_id TEXT;
ALTER TABLE agent_session ADD COLUMN IF NOT EXISTS causation_depth INT NOT NULL DEFAULT 0;
ALTER TABLE agent_session ADD COLUMN IF NOT EXISTS chain_trigger BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE agent_session ADD COLUMN IF NOT EXISTS max_chain_depth INT NOT NULL DEFAULT 10;
ALTER TABLE agent_session ADD COLUMN IF NOT EXISTS allowed_tools JSONB;
ALTER TABLE agent_session ADD COLUMN IF NOT EXISTS system_prompt TEXT;

-- --- evaluation ---
CREATE TABLE IF NOT EXISTS gold_set_question (
    id BIGSERIAL PRIMARY KEY,
    question_text TEXT NOT NULL,
    category TEXT NOT NULL,
    expected_urn_substring TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eval_run (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    metrics JSONB
);

-- --- ML model registry ---
CREATE TABLE IF NOT EXISTS model_registration (
    name TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    version TEXT NOT NULL,
    framework TEXT NOT NULL,
    artifact_key TEXT NOT NULL,
    input_schema JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --- spend limits ---
CREATE TABLE IF NOT EXISTS intelligence_request_window (
    principal_urn TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    request_count INT NOT NULL DEFAULT 0,
    PRIMARY KEY (principal_urn, window_start)
);

CREATE TABLE IF NOT EXISTS intelligence_token_day (
    tenant_id TEXT NOT NULL,
    day DATE NOT NULL,
    tokens BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, day)
);

-- --- tool plugins (holon_common.plugin shape) ---
CREATE TABLE IF NOT EXISTS plugin_registration (
    name TEXT PRIMARY KEY,
    plugin_type TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest JSONB NOT NULL,
    checksum TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --- audit_event (intelligence copy of holon_common.audit_store) ---
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

-- --- event_outbox (intelligence copy of holon_common.outbox) ---
CREATE TABLE IF NOT EXISTS event_outbox (
    id BIGSERIAL PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    envelope JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ
);
