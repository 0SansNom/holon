-- Pipeline names are unique per tenant, not globally.
ALTER TABLE pipeline_definition DROP CONSTRAINT IF EXISTS pipeline_definition_pkey;
ALTER TABLE pipeline_definition ADD PRIMARY KEY (tenant_id, name);
