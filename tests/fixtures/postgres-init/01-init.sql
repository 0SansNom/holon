CREATE DATABASE source_erp;
CREATE DATABASE holon_identity;
CREATE DATABASE holon_connectivity;
CREATE DATABASE holon_knowledge;
CREATE DATABASE holon_automation;
CREATE DATABASE holon_intelligence;
CREATE DATABASE holon_experience;
-- Iceberg REST catalog's own JdbcCatalog metadata store — Postgres
-- instead of embedded SQLite, which is single-writer and subject to
-- concurrency locks under parallel /sync requests ([SQLITE_BUSY]).
CREATE DATABASE holon_iceberg_catalog;
-- SpiceDB relationship store (Authzed datastore).
CREATE DATABASE holon_spicedb;
