ALTER TABLE principal ALTER COLUMN client_secret DROP NOT NULL;
ALTER TABLE principal ADD COLUMN IF NOT EXISTS client_secret_hash TEXT;
