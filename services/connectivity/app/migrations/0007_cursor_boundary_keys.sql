-- Identities already ingested at the current cursor value. Inclusive
-- resume (`cursor >= last`) re-reads that boundary; these keys drop the
-- copies so a row that shares the value and arrives later is kept once.
ALTER TABLE sql_source ADD COLUMN IF NOT EXISTS cursor_boundary_keys TEXT;
ALTER TABLE salesforce_source ADD COLUMN IF NOT EXISTS cursor_boundary_keys TEXT;
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS cursor_boundary_keys TEXT;
