-- Pipelines belong to a workspace like sources. NULL = the deployment's
-- default workspace (rows created before this column existed).
ALTER TABLE pipeline_definition ADD COLUMN IF NOT EXISTS workspace_id TEXT;
