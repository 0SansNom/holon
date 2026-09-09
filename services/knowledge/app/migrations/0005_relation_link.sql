-- Materialized M:N join_dataset pairs. Catalog ingest writes these from
-- Iceberg once per sync; object-graph reads query this table (plus
-- relation_link_overlay), never scan the warehouse on the request path.

CREATE TABLE IF NOT EXISTS relation_link (
    tenant_id TEXT NOT NULL,
    relation_urn TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    source_snapshot_id BIGINT NOT NULL,
    materialized_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, relation_urn, source_id, target_id)
);

CREATE INDEX IF NOT EXISTS relation_link_source_idx
    ON relation_link (tenant_id, relation_urn, source_id);

CREATE INDEX IF NOT EXISTS relation_link_target_idx
    ON relation_link (tenant_id, relation_urn, target_id);
