-- Last snapshot materialized per ObjectType. A type with no row here has
-- never been materialized, which a read by key reports distinctly from an
-- absent instance. An empty snapshot still leaves a row.

CREATE TABLE IF NOT EXISTS object_type_materialization (
    object_type TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    source_snapshot_id BIGINT NOT NULL,
    materialized_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (object_type, tenant_id)
);

-- Types materialized before this table existed; one whose last snapshot was
-- empty has no rows left to infer it from and is recorded on its next sync.
INSERT INTO object_type_materialization (object_type, tenant_id, source_snapshot_id, materialized_at)
SELECT object_type, tenant_id, MAX(source_snapshot_id), MAX(materialized_at)
FROM object_instance
GROUP BY object_type, tenant_id
ON CONFLICT (object_type, tenant_id) DO NOTHING;
