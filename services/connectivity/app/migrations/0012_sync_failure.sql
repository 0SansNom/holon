-- Failed and timed-out syncs. sync_run keeps successes only, so its readers stay unchanged.
CREATE TABLE IF NOT EXISTS sync_failure (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    connector_urn TEXT NOT NULL,
    dataset_urn TEXT NOT NULL,
    error_name TEXT NOT NULL,
    error TEXT NOT NULL,
    timed_out BOOLEAN NOT NULL DEFAULT false,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS sync_failure_tenant_idx ON sync_failure (tenant_id, id DESC);
