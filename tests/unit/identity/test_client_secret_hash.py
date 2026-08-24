"""Tests for client_secret hashing and lazy legacy-plaintext migration."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "identity"))

from app.seed import (  # noqa: E402
    _verify_client_secret_hash,
    hash_client_secret,
    verify_and_migrate_secret,
)


def test_hash_roundtrip_verifies_correct_secret_and_rejects_wrong_one():
    stored = hash_client_secret("correct-horse-battery-staple")
    assert _verify_client_secret_hash("correct-horse-battery-staple", stored)
    assert not _verify_client_secret_hash("wrong-guess", stored)


def test_hash_is_salted_so_repeats_differ():
    a = hash_client_secret("same-input")
    b = hash_client_secret("same-input")
    assert a != b
    assert _verify_client_secret_hash("same-input", a)
    assert _verify_client_secret_hash("same-input", b)


def _pool() -> MagicMock:
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="UPDATE 1")
    return pool


def test_verify_and_migrate_secret_hash_only_row_never_touches_db():
    pool = _pool()
    row = {"urn": "hl:acme:global:user:x", "client_secret": None, "client_secret_hash": hash_client_secret("s3cret")}

    ok = asyncio.run(verify_and_migrate_secret(pool, row, "s3cret"))

    assert ok is True
    pool.execute.assert_not_awaited()


def test_verify_and_migrate_secret_legacy_plaintext_row_migrates_on_success():
    pool = _pool()
    row = {"urn": "hl:acme:global:user:legacy", "client_secret": "old-plaintext", "client_secret_hash": None}

    ok = asyncio.run(verify_and_migrate_secret(pool, row, "old-plaintext"))

    assert ok is True
    pool.execute.assert_awaited_once()
    sql, hashed, urn = pool.execute.await_args.args
    assert "client_secret_hash" in sql
    assert urn == row["urn"]
    assert _verify_client_secret_hash("old-plaintext", hashed)


def test_verify_and_migrate_secret_rejects_wrong_legacy_plaintext_without_migrating():
    pool = _pool()
    row = {"urn": "hl:acme:global:user:legacy", "client_secret": "old-plaintext", "client_secret_hash": None}

    ok = asyncio.run(verify_and_migrate_secret(pool, row, "wrong"))

    assert ok is False
    pool.execute.assert_not_awaited()


def test_verify_and_migrate_secret_no_secret_at_all_rejects():
    pool = _pool()
    row = {"urn": "hl:acme:global:user:ghost", "client_secret": None, "client_secret_hash": None}

    ok = asyncio.run(verify_and_migrate_secret(pool, row, "anything"))

    assert ok is False
    pool.execute.assert_not_awaited()
