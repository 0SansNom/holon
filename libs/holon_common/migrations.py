"""Versioned SQL migrations — numbered `.sql` files per service, tracked
in a `schema_migrations` table, each applied once inside its own
transaction.

Deliberately not an ORM or Alembic: most services still create tables via
`ensure_schema()` (`CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... ADD
COLUMN IF NOT EXISTS`, re-run idempotently on every boot). That pattern
only ever covers *additive* change and carries no history — this adds
exactly what it was missing: a real place for a change that isn't purely
additive (rename, drop, backfill) to live, with a version so it's obvious
what ran and when.

Knowledge and Identity are migrations-first. Domain `ensure_schema()`
helpers were removed there; baselines are
`services/knowledge/app/migrations/0000_baseline.sql` and
`services/identity/app/migrations/0000_baseline.sql`, then `0001`–.
Other services still call `ensure_schema()` at boot *before*
`run_migrations`. This runner governs schema changes from here forward;
it is not a retroactive rewrite of what's already shipped.

Checksums: each applied file's SHA-256 is recorded. Editing an already-
applied file fails boot instead of drifting silently.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import asyncpg

logger = logging.getLogger("holon_common.migrations")

_FILENAME_RE = re.compile(r"^\d{4}_[a-z0-9_]+\.sql$")

# Arbitrary but fixed — every replica of the same service contends on
# this same key (advisory locks are scoped per-database, and each
# service already owns its own database, so one constant is fine across
# all of them). Only its stability matters, not its value.
_ADVISORY_LOCK_KEY = 47281900


class MigrationChecksumError(RuntimeError):
    """Raised when an already-applied migration file no longer matches
    the checksum recorded at apply time."""


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


async def run_migrations(pool: asyncpg.Pool, migrations_dir: Path) -> list[str]:
    """Applies every `NNNN_description.sql` file in `migrations_dir` not
    yet recorded in `schema_migrations`, oldest first by filename.

    Concurrency: every replica of a service calls this at startup. A
    Postgres advisory lock serializes them — whichever replica arrives
    first runs whatever is pending while the rest block on the lock, then
    each of those finds nothing left to apply and returns immediately.
    Never two replicas racing the same migration.

    A missing `migrations_dir` (a service with no migrations yet) is not
    an error — returns an empty list.
    """
    if not migrations_dir.is_dir():
        return []
    files = sorted(p for p in migrations_dir.glob("*.sql") if _FILENAME_RE.match(p.name))
    applied: list[str] = []
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", _ADVISORY_LOCK_KEY)
        try:
            # Must happen after the lock — a fresh DB's first concurrent boot can
            # still race Postgres's non-atomic CREATE TABLE IF NOT EXISTS.
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version TEXT PRIMARY KEY, "
                "checksum TEXT, "
                "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            await conn.execute(
                "ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS checksum TEXT"
            )
            already_rows = await conn.fetch("SELECT version, checksum FROM schema_migrations")
            already = {r["version"]: r["checksum"] for r in already_rows}
            for path in files:
                sql = path.read_text()
                digest = _checksum(sql)
                if path.name in already:
                    recorded = already[path.name]
                    if recorded is None:
                        await conn.execute(
                            "UPDATE schema_migrations SET checksum = $1 WHERE version = $2",
                            digest,
                            path.name,
                        )
                        logger.warning(
                            "backfilled checksum for legacy migration %s", path.name
                        )
                    elif recorded != digest:
                        raise MigrationChecksumError(
                            f"migration {path.name} checksum mismatch: "
                            f"recorded={recorded} file={digest} — "
                            "do not edit applied migrations; add a new file"
                        )
                    continue
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)",
                        path.name,
                        digest,
                    )
                logger.info("applied migration %s", path.name)
                applied.append(path.name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _ADVISORY_LOCK_KEY)
    return applied
