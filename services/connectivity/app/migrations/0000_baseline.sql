-- Connectivity schema baseline (pre-0001). CREATE/ALTER IF NOT EXISTS so this
-- is a no-op on databases that already ran ensure_schema(). Fresh installs
-- get tables here; 0001 then applies the non-additive follow-up
-- (generic_rest_connection.auth_header_* DROP NOT NULL).
-- Keep auth_header_name/value NOT NULL in CREATE so 0001 remains correct.

-- --- sync / runtime ---
CREATE TABLE IF NOT EXISTS sync_run (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    connector_urn TEXT NOT NULL,
    dataset_urn TEXT NOT NULL,
    dataset_version_urn TEXT NOT NULL,
    iceberg_namespace TEXT NOT NULL,
    iceberg_table TEXT NOT NULL,
    snapshot_id BIGINT NOT NULL,
    row_count INTEGER NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL
);

-- Cluster-wide flags (quiesce) so multi-replica Connectivity shares state.
CREATE TABLE IF NOT EXISTS connectivity_runtime (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --- plugins ---
CREATE TABLE IF NOT EXISTS plugin_registration (
    name TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    manifest JSONB NOT NULL,
    checksum TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- NULL tenant_id = global plugin (e.g. exchange-rate); non-null = tenant-scoped.
ALTER TABLE plugin_registration ADD COLUMN IF NOT EXISTS tenant_id TEXT;
-- Same scheduling model as generic_rest_source — NULL = manual only.
ALTER TABLE plugin_registration ADD COLUMN IF NOT EXISTS schedule_interval_minutes INTEGER;

-- --- generic REST sources / connections ---
CREATE TABLE IF NOT EXISTS generic_rest_source (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    auth_header_name TEXT,
    auth_header_value TEXT,
    record_path TEXT,
    next_page_path TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS next_page_path TEXT;
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS secret_ref TEXT;
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS connection_name TEXT;
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS schedule_interval_minutes INTEGER;
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS cursor_property TEXT;
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS incremental_param TEXT;
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS last_cursor_value TEXT;
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS workspace_id TEXT;

-- auth_header_* NOT NULL here so 0001_oauth2_connection_auth can DROP NOT NULL.
CREATE TABLE IF NOT EXISTS generic_rest_connection (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    auth_header_name TEXT NOT NULL,
    auth_header_value TEXT NOT NULL DEFAULT '',
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

ALTER TABLE generic_rest_connection ADD COLUMN IF NOT EXISTS secret_ref TEXT;
ALTER TABLE generic_rest_connection ADD COLUMN IF NOT EXISTS auth_type TEXT NOT NULL DEFAULT 'header';
ALTER TABLE generic_rest_connection ADD COLUMN IF NOT EXISTS oauth2_token_url TEXT;
ALTER TABLE generic_rest_connection ADD COLUMN IF NOT EXISTS oauth2_client_id TEXT;
ALTER TABLE generic_rest_connection ADD COLUMN IF NOT EXISTS oauth2_client_secret TEXT;
ALTER TABLE generic_rest_connection ADD COLUMN IF NOT EXISTS oauth2_scope TEXT;
ALTER TABLE generic_rest_connection ADD COLUMN IF NOT EXISTS oauth2_cached_token TEXT;
ALTER TABLE generic_rest_connection ADD COLUMN IF NOT EXISTS oauth2_token_expires_at TIMESTAMPTZ;

-- --- SQL sources / connections ---
CREATE TABLE IF NOT EXISTS sql_connection (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 5432,
    database TEXT NOT NULL,
    username TEXT NOT NULL,
    password TEXT,
    secret_ref TEXT,
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS sql_source (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    connection_name TEXT NOT NULL,
    table_name TEXT,
    query TEXT,
    schedule_interval_minutes INTEGER,
    cursor_property TEXT,
    last_cursor_value TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

-- --- object storage sources / connections ---
CREATE TABLE IF NOT EXISTS object_connection (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT 'us-east-1',
    access_key_id TEXT NOT NULL,
    secret_access_key TEXT,
    secret_ref TEXT,
    path_style BOOLEAN NOT NULL DEFAULT true,
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

-- 's3' or 'azure' (Blob Storage).
ALTER TABLE object_connection ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 's3';

CREATE TABLE IF NOT EXISTS object_source (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    connection_name TEXT NOT NULL,
    bucket TEXT NOT NULL,
    object_key TEXT,
    key_prefix TEXT,
    format TEXT NOT NULL,
    incremental BOOLEAN NOT NULL DEFAULT false,
    last_synced_key TEXT,
    schedule_interval_minutes INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

-- --- pipelines ---
CREATE TABLE IF NOT EXISTS pipeline_definition (
    name TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    steps JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE pipeline_definition ADD COLUMN IF NOT EXISTS schedule_interval_minutes INTEGER;

CREATE TABLE IF NOT EXISTS pipeline_run (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    pipeline_name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    step_results JSONB NOT NULL DEFAULT '[]',
    error TEXT
);

-- --- write targets ---
CREATE TABLE IF NOT EXISTS write_target (
    tenant_id TEXT NOT NULL,
    dataset_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    id_column TEXT NOT NULL,
    allowed_properties JSONB NOT NULL,
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, dataset_name)
);

-- --- Kafka streams ---
CREATE TABLE IF NOT EXISTS kafka_stream_source (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    topic TEXT NOT NULL,
    key_field TEXT NOT NULL,
    dataset_name TEXT NOT NULL,
    batch_interval_seconds DOUBLE PRECISION NOT NULL DEFAULT 5.0,
    status TEXT NOT NULL DEFAULT 'active',
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS kafka_stream_state (
    tenant_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    record_key TEXT NOT NULL,
    data JSONB NOT NULL,
    updated_row_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, source_name, record_key)
);

-- --- audit_event (connectivity copy of holon_common.audit_store) ---
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

-- --- event_outbox (connectivity copy of holon_common.outbox) ---
CREATE TABLE IF NOT EXISTS event_outbox (
    id BIGSERIAL PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    envelope JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ
);
