-- Watermark of the Iceberg snapshot last projected into relation_link.
-- Lets ingest skip a full replace when the bridge snapshot has not moved,
-- and lets graph reads tell "never materialized" from "materialized empty".

CREATE TABLE IF NOT EXISTS relation_link_sync (
    tenant_id TEXT NOT NULL,
    relation_urn TEXT NOT NULL,
    source_snapshot_id BIGINT NOT NULL,
    pair_count INTEGER NOT NULL DEFAULT 0,
    materialized_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, relation_urn)
);
