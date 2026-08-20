-- Database per platform (R4.1) — each platform has its own physical schema.
-- source_erp simulates the external system that Connectivity reads. R9.3: never modified
-- by a connector, except for an explicitly declared write ontology Action,
-- approved and audited — see POST /source/customers/{id}/close-account.
--
-- Infra only, no fixture rows: source_erp's schema+data is test-only and
-- lives entirely in seed/source_erp.sql (make seed), not baked into
-- every fresh boot — see docs/ops/seed-data.md.
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
