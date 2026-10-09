-- Optional verified TLS for SQL connections. Existing rows stay cleartext;
-- AlloyDB, CockroachDB, and Synapse opt in via the register default.
ALTER TABLE sql_connection ADD COLUMN IF NOT EXISTS use_tls BOOLEAN NOT NULL DEFAULT false;
