-- Who produced a dataset version: a connector sync or a pipeline step.
-- Null on rows catalogued before this column existed.

ALTER TABLE dataset_version ADD COLUMN IF NOT EXISTS producer JSONB;
