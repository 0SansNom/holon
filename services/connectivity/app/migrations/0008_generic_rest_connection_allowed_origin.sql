-- Origin (scheme://host[:port]) a REST connection's credential may be sent to.
-- NULL on rows created before this column: those connections refuse to sync
-- until an editor sets it (and re-enters the secret).
ALTER TABLE generic_rest_connection ADD COLUMN IF NOT EXISTS allowed_origin TEXT;
