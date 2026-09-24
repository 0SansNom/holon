-- No-code SFTP connections / sources (password or secret_ref auth).
CREATE TABLE IF NOT EXISTS sftp_connection (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 22,
    username TEXT NOT NULL,
    password TEXT,
    secret_ref TEXT,
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS sftp_source (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    connection_name TEXT NOT NULL,
    remote_path TEXT,
    remote_prefix TEXT,
    format TEXT NOT NULL,
    incremental BOOLEAN NOT NULL DEFAULT false,
    last_synced_path TEXT,
    schedule_interval_minutes INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);
