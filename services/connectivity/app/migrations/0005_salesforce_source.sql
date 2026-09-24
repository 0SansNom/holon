-- No-code Salesforce connections / sources (Connected App client credentials + SOQL).
CREATE TABLE IF NOT EXISTS salesforce_connection (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    login_url TEXT NOT NULL DEFAULT 'https://login.salesforce.com',
    client_id TEXT NOT NULL,
    client_secret TEXT,
    secret_ref TEXT,
    oauth2_cached_token TEXT,
    oauth2_token_expires_at TIMESTAMPTZ,
    instance_url TEXT,
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS salesforce_source (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    connection_name TEXT NOT NULL,
    soql TEXT NOT NULL,
    api_version TEXT NOT NULL DEFAULT 'v59.0',
    cursor_property TEXT,
    last_cursor_value TEXT,
    schedule_interval_minutes INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);
