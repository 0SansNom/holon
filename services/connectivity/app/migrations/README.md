# Migrations

Applied once each, in filename order, by `holon_common.run_migrations`
(called from this service's `lifespan()`). Tracked in the
`schema_migrations` table — never edit a file after it has shipped;
write a new one instead, even to fix a mistake in an already-applied
migration.

This exists for schema changes that aren't purely additive — a rename, a
drop, a backfill, a data migration. Purely additive changes (a new
table, a new nullable column) can still go through the service's own
`ensure_schema()` (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD
COLUMN IF NOT EXISTS`) if that's simpler; both run at every startup,
`ensure_schema()` first.

## Convention

- Filename: `NNNN_short_description.sql` — four-digit, zero-padded,
  sequential, lowercase, underscores. `0001_add_widget_kind_column.sql`.
  A name that doesn't match this pattern is silently skipped by the
  runner (not an error, not logged) — a typo in the number or an
  uppercase letter means the file quietly never runs.
- One concern per file.
- Plain SQL, no explicit `BEGIN`/`COMMIT`/`ROLLBACK` — `run_migrations`
  already wraps the whole file in its own `conn.transaction()`. A
  `COMMIT` inside the file would close that transaction out from under
  asyncpg, which then errors trying to commit or roll back a transaction
  that's no longer open. If a change genuinely needs a savepoint, use
  `SAVEPOINT`/`RELEASE SAVEPOINT` inside the already-open transaction,
  never a nested `BEGIN`.
- Never edit or delete a merged migration file, even to fix a typo in a
  comment — the `schema_migrations` table records it by filename only,
  **not a content checksum**, so an edited file silently diverges from
  what already ran on any database that already applied it (the runner
  has no way to detect this). Ship a follow-up migration instead.
