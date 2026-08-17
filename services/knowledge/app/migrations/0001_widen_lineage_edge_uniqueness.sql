-- Widens lineage_edge's uniqueness from (source_urn, target_urn, relation)
-- to include (source_column, target_property), so a dataset-level edge
-- and each of its column-level edges can coexist. Previously replayed on
-- every boot via lineage.py's ensure_schema() — non-additive (no ADD
-- CONSTRAINT IF NOT EXISTS in Postgres), which raced two replicas
-- against each other ("relation already exists"). Runs exactly once here
-- instead, serialized by the migration runner's advisory lock.
ALTER TABLE lineage_edge DROP CONSTRAINT IF EXISTS lineage_edge_source_urn_target_urn_relation_key;
ALTER TABLE lineage_edge DROP CONSTRAINT IF EXISTS lineage_edge_full_key;
ALTER TABLE lineage_edge ADD CONSTRAINT lineage_edge_full_key
    UNIQUE (source_urn, target_urn, relation, source_column, target_property);
