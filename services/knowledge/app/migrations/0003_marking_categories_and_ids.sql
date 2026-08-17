-- Marking categories + stable UUID on markings.
-- Names stay unique per tenant so existing SpiceDB URNs and OT/instance
-- JSONB lists of marking names keep working.

CREATE TABLE IF NOT EXISTS marking_category (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category_type TEXT NOT NULL CHECK (category_type IN ('CONJUNCTIVE', 'DISJUNCTIVE')),
    marking_type TEXT NOT NULL DEFAULT 'MANDATORY' CHECK (marking_type IN ('MANDATORY')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

ALTER TABLE marking ADD COLUMN IF NOT EXISTS id UUID;
ALTER TABLE marking ADD COLUMN IF NOT EXISTS category_id UUID;

-- One Default category per tenant that already has markings (or any tenant
-- row we can see from marking). Fresh tenants get a category on first create.
INSERT INTO marking_category (id, tenant_id, name, description, category_type, marking_type)
SELECT gen_random_uuid(), t.tenant_id, 'Default', 'Default mandatory marking category', 'CONJUNCTIVE', 'MANDATORY'
FROM (SELECT DISTINCT tenant_id FROM marking) AS t
WHERE NOT EXISTS (
    SELECT 1 FROM marking_category c WHERE c.tenant_id = t.tenant_id AND c.name = 'Default'
);

UPDATE marking m
SET id = COALESCE(m.id, gen_random_uuid()),
    category_id = COALESCE(
        m.category_id,
        (SELECT c.id FROM marking_category c WHERE c.tenant_id = m.tenant_id AND c.name = 'Default' LIMIT 1)
    )
WHERE m.id IS NULL OR m.category_id IS NULL;

ALTER TABLE marking ALTER COLUMN id SET NOT NULL;
ALTER TABLE marking ALTER COLUMN category_id SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'marking_id_key'
    ) THEN
        ALTER TABLE marking ADD CONSTRAINT marking_id_key UNIQUE (id);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'marking_category_id_fkey'
    ) THEN
        ALTER TABLE marking
            ADD CONSTRAINT marking_category_id_fkey
            FOREIGN KEY (category_id) REFERENCES marking_category(id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS marking_category_tenant_idx ON marking_category (tenant_id);
CREATE INDEX IF NOT EXISTS marking_tenant_category_idx ON marking (tenant_id, category_id);
