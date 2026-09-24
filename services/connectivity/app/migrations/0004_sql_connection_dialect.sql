-- SQL source connections support postgres | mysql | mssql drivers.
-- Existing rows keep the previous Postgres-wire behaviour.
ALTER TABLE sql_connection ADD COLUMN IF NOT EXISTS dialect TEXT NOT NULL DEFAULT 'postgres';
