-- Identity schema baseline (pre-0001). CREATE/ALTER IF NOT EXISTS so this
-- is a no-op on databases that already ran ensure_schema(). Fresh installs
-- get tables here; 0001–0002 then apply the non-additive follow-ups
-- (client_secret nullable + hash, external_id).

-- --- tenant / workspace / principal ---
CREATE TABLE IF NOT EXISTS tenant (
    tenant_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS workspace (
    workspace_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenant(tenant_id),
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS principal (
    urn TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    on_behalf_of TEXT,
    country TEXT,
    client_secret TEXT NOT NULL DEFAULT 'unset',
    status TEXT NOT NULL DEFAULT 'active',
    oidc_sub TEXT
);

-- additive for databases seeded before these columns existed
ALTER TABLE principal ADD COLUMN IF NOT EXISTS country TEXT;
ALTER TABLE principal ADD COLUMN IF NOT EXISTS client_secret TEXT NOT NULL DEFAULT 'unset';
ALTER TABLE principal ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE principal ADD COLUMN IF NOT EXISTS oidc_sub TEXT;
ALTER TABLE tenant ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE workspace ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';

CREATE TABLE IF NOT EXISTS project (
    urn TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS principal_oidc_sub_uidx
    ON principal (oidc_sub) WHERE oidc_sub IS NOT NULL;

CREATE TABLE IF NOT EXISTS oidc_pending_state (
    state TEXT PRIMARY KEY,
    verifier TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS saml_seen_assertion (
    assertion_id TEXT PRIMARY KEY,
    seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --- JWT revocation (jti denylist until exp) ---
CREATE TABLE IF NOT EXISTS revoked_token (
    jti TEXT PRIMARY KEY,
    principal_urn TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS revoked_token_expires_idx
    ON revoked_token (expires_at);

-- --- audit_event (identity copy of holon_common.audit_store) ---
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

-- --- event_outbox (identity copy of holon_common.outbox) ---
CREATE TABLE IF NOT EXISTS event_outbox (
    id BIGSERIAL PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    envelope JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ
);
