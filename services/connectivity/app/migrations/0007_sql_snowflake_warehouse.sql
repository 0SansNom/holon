-- Optional Snowflake compute warehouse for dialect='snowflake' SQL connections.
ALTER TABLE sql_connection ADD COLUMN IF NOT EXISTS warehouse TEXT;
