-- Relaxes NOT NULL on columns that predate two later features which both
-- need them optional: generic resource branching (ontology_branch's
-- object_type_urn/version stay NULL for a non-ObjectType branch) and
-- interface-scoped Actions (action_type.target_object_type stays NULL
-- when target_interface is set instead). DROP NOT NULL is naturally
-- idempotent (a no-op on an already-nullable column, never an error) so
-- this was never racy the way 0001's constraint rewrite was — moved here
-- anyway for the same reason: a real, versioned home for non-additive
-- change, instead of another line in ensure_schema()'s replayed-every-
-- boot DDL string.
ALTER TABLE ontology_branch ALTER COLUMN object_type_urn DROP NOT NULL;
ALTER TABLE ontology_branch ALTER COLUMN version DROP NOT NULL;
ALTER TABLE action_type ALTER COLUMN target_object_type DROP NOT NULL;
