"""White-box tests for `holon_common.migrations.run_migrations` — no
HTTP surface exists for schema migrations, so this connects directly to
Postgres, same convention `test_bitemporal_history.py` already uses for
direct source-database access. Requires the stack running (`make up`);
uses the `holon_knowledge` database (already running, already reachable
on localhost) purely as a place to create and drop a throwaway table —
never touches any real service table.

Each test wraps its whole body in exactly one `asyncio.run()` call —
an asyncpg pool/connection is bound to the event loop that created it,
so splitting one test's pool across multiple separate `asyncio.run()`
calls (e.g. via reusable fixtures) raises "another operation is in
progress"; a single coroutine per test sidesteps that rather than
pulling in pytest-asyncio for one file.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libs"))
from holon_common.migrations import run_migrations  # noqa: E402

DB_URL = f"postgresql://holon:{os.environ.get('POSTGRES_PASSWORD', 'holon12345')}@localhost:5432/holon_knowledge"


def _write(tmp_path: Path, name: str, sql: str) -> None:
    (tmp_path / name).write_text(sql)


async def _cleanup(conn: asyncpg.Connection, table: str, versions: list[str]) -> None:
    await conn.execute(f"DROP TABLE IF EXISTS {table}")
    if versions:
        await conn.execute("DELETE FROM schema_migrations WHERE version = ANY($1)", versions)


def test_a_pending_migration_is_applied_and_recorded(tmp_path) -> None:
    table = f"migration_test_{uuid.uuid4().hex[:12]}"
    _write(tmp_path, "0001_create_table.sql", f"CREATE TABLE {table} (id INT PRIMARY KEY);")

    async def _body() -> None:
        pool = await asyncpg.create_pool(dsn=DB_URL, min_size=1, max_size=5)
        try:
            applied = await run_migrations(pool, tmp_path)
            assert applied == ["0001_create_table.sql"]

            async with pool.acquire() as conn:
                exists = await conn.fetchval("SELECT to_regclass($1)", table)
                assert exists == table

                recorded = await conn.fetchval(
                    "SELECT version FROM schema_migrations WHERE version = $1", "0001_create_table.sql"
                )
                assert recorded == "0001_create_table.sql"
        finally:
            async with pool.acquire() as conn:
                await _cleanup(conn, table, ["0001_create_table.sql"])
            await pool.close()

    asyncio.run(_body())


def test_an_already_applied_migration_is_not_reapplied(tmp_path) -> None:
    table = f"migration_test_{uuid.uuid4().hex[:12]}"
    _write(tmp_path, "0001_create_table.sql", f"CREATE TABLE {table} (id INT PRIMARY KEY);")

    async def _body() -> None:
        pool = await asyncpg.create_pool(dsn=DB_URL, min_size=1, max_size=5)
        try:
            first = await run_migrations(pool, tmp_path)
            assert first == ["0001_create_table.sql"]

            second = await run_migrations(pool, tmp_path)
            assert second == []  # nothing left to apply — no error, no re-run
        finally:
            async with pool.acquire() as conn:
                await _cleanup(conn, table, ["0001_create_table.sql"])
            await pool.close()

    asyncio.run(_body())


def test_a_filename_not_matching_the_convention_is_silently_skipped(tmp_path) -> None:
    table = f"migration_test_{uuid.uuid4().hex[:12]}"
    _write(tmp_path, "not_a_migration.sql", f"CREATE TABLE {table} (id INT PRIMARY KEY);")
    _write(tmp_path, "1_too_short.sql", f"CREATE TABLE {table} (id INT PRIMARY KEY);")

    async def _body() -> None:
        pool = await asyncpg.create_pool(dsn=DB_URL, min_size=1, max_size=5)
        try:
            applied = await run_migrations(pool, tmp_path)
            assert applied == []

            async with pool.acquire() as conn:
                exists = await conn.fetchval("SELECT to_regclass($1)", table)
                assert exists is None
        finally:
            async with pool.acquire() as conn:
                await _cleanup(conn, table, [])
            await pool.close()

    asyncio.run(_body())


def test_a_missing_migrations_directory_is_not_an_error(tmp_path) -> None:
    missing = tmp_path / "does-not-exist"

    async def _body() -> None:
        pool = await asyncpg.create_pool(dsn=DB_URL, min_size=1, max_size=5)
        try:
            applied = await run_migrations(pool, missing)
            assert applied == []
        finally:
            await pool.close()

    asyncio.run(_body())


def test_concurrent_replicas_apply_a_pending_migration_exactly_once(tmp_path) -> None:
    """The real scenario this exists for: several replicas of the same
    service calling `run_migrations` at boot, racing the same new file.
    """
    table = f"migration_test_{uuid.uuid4().hex[:12]}"
    _write(tmp_path, "0001_create_table.sql", f"CREATE TABLE {table} (id INT PRIMARY KEY);")

    async def _body() -> None:
        pool = await asyncpg.create_pool(dsn=DB_URL, min_size=3, max_size=6)
        try:
            results = await asyncio.gather(
                run_migrations(pool, tmp_path),
                run_migrations(pool, tmp_path),
                run_migrations(pool, tmp_path),
            )
            # Exactly one replica applies it; the other two find nothing pending.
            applied_counts = sorted(len(r) for r in results)
            assert applied_counts == [0, 0, 1]

            async with pool.acquire() as conn:
                count = await conn.fetchval(
                    "SELECT count(*) FROM schema_migrations WHERE version = $1", "0001_create_table.sql"
                )
                assert count == 1  # never double-recorded
        finally:
            async with pool.acquire() as conn:
                await _cleanup(conn, table, ["0001_create_table.sql"])
            await pool.close()

    asyncio.run(_body())


def test_editing_an_applied_migration_fails_checksum(tmp_path) -> None:
    table = f"migration_test_{uuid.uuid4().hex[:12]}"
    _write(tmp_path, "0001_create_table.sql", f"CREATE TABLE {table} (id INT PRIMARY KEY);")

    async def _body() -> None:
        pool = await asyncpg.create_pool(dsn=DB_URL, min_size=1, max_size=5)
        try:
            await run_migrations(pool, tmp_path)
            _write(tmp_path, "0001_create_table.sql", f"CREATE TABLE {table} (id INT PRIMARY KEY, drift INT);")
            from holon_common.migrations import MigrationChecksumError

            try:
                await run_migrations(pool, tmp_path)
                raise AssertionError("expected MigrationChecksumError")
            except MigrationChecksumError:
                pass
        finally:
            async with pool.acquire() as conn:
                await _cleanup(conn, table, ["0001_create_table.sql"])
            await pool.close()

    asyncio.run(_body())
