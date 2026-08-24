ALTER TABLE principal ADD COLUMN IF NOT EXISTS external_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS principal_external_id_uidx
    ON principal (external_id) WHERE external_id IS NOT NULL;
