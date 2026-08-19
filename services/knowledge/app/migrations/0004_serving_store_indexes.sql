-- Serving-store lookup indexes. Fresh installs also create these from
-- `serving_store.DDL`; this file covers existing databases that already
-- ran `CREATE TABLE IF NOT EXISTS` without the indexes.

CREATE INDEX IF NOT EXISTS object_instance_lookup
    ON object_instance (object_type, tenant_id, instance_id);

CREATE INDEX IF NOT EXISTS object_instance_history_as_of
    ON object_instance_history (object_type, tenant_id, instance_id, recorded_at DESC);

CREATE INDEX IF NOT EXISTS object_instance_tombstone_lookup
    ON object_instance_tombstone (tenant_id, object_type, instance_id);
