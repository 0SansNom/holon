"""Shared source-registry base: secrets, dataset conflicts, deferred cursor commits."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from holon_common.connector_safety import ConnectorSafetyError  # noqa: E402

from app.source_registry_base import (  # noqa: E402
    ConnectionConflictError,
    SourceConflictError,
    SourceFetchError,
    assert_dataset_available,
    delete_row,
    get_row,
    is_registered,
    list_rows,
    make_column_cursor_commit,
    make_property_cursor_commit,
    resolve_source_secret,
    set_status,
)
from app import (  # noqa: E402
    generic_source_registry,
    object_source_registry,
    salesforce_source_registry,
    sftp_source_registry,
    sql_source_registry,
)

pytestmark = pytest.mark.unit


def test_exception_types_are_shared_across_registries() -> None:
    shared = SourceFetchError
    for mod in (
        generic_source_registry,
        sql_source_registry,
        object_source_registry,
        sftp_source_registry,
        salesforce_source_registry,
    ):
        assert mod.SourceFetchError is shared
        assert mod.SourceConflictError is SourceConflictError
        assert issubclass(mod.SourceFetchError, ValueError)


def test_connection_conflict_error_is_shared() -> None:
    for mod in (generic_source_registry, sql_source_registry, object_source_registry):
        assert mod.ConnectionConflictError is ConnectionConflictError
        assert issubclass(mod.ConnectionConflictError, ValueError)


def test_row_helpers_reject_unknown_table_before_query() -> None:
    pool = MagicMock()
    pool.fetchrow = AsyncMock()
    pool.fetch = AsyncMock()
    pool.fetchval = AsyncMock()
    pool.execute = AsyncMock()
    calls = (
        get_row(pool, table="pg_user", columns="name", tenant_id="t1", name="orders"),
        list_rows(pool, table="pg_user", columns="name", tenant_id="t1"),
        delete_row(pool, table="pg_user", tenant_id="t1", name="orders"),
        set_status(pool, table="pg_user", columns="name", tenant_id="t1", name="orders", status="active"),
        is_registered(pool, table="pg_user", tenant_id="t1", name="orders"),
    )
    for call in calls:
        with pytest.raises(ValueError, match="unknown source table"):
            asyncio.run(call)
    pool.fetchrow.assert_not_awaited()
    pool.fetch.assert_not_awaited()
    pool.fetchval.assert_not_awaited()
    pool.execute.assert_not_awaited()


def test_get_row_reads_allowlisted_table() -> None:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"name": "orders"})
    row = asyncio.run(
        get_row(pool, table="sql_source", columns="tenant_id, name", tenant_id="t1", name="orders")
    )
    assert row == {"name": "orders"}
    sql, tenant_id, name = pool.fetchrow.await_args.args
    assert sql == "SELECT tenant_id, name FROM sql_source WHERE tenant_id = $1 AND name = $2"
    assert tenant_id == "t1"
    assert name == "orders"


def test_get_row_rejects_column_list_before_query() -> None:
    pool = MagicMock()
    pool.fetchrow = AsyncMock()
    with pytest.raises(ValueError, match="column list"):
        asyncio.run(get_row(pool, table="sql_source", columns="name; drop", tenant_id="t1", name="orders"))
    pool.fetchrow.assert_not_awaited()


def test_set_status_updates_then_rereads() -> None:
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"name": "orders", "status": "disabled"})
    row = asyncio.run(
        set_status(
            pool,
            table="object_source",
            columns="name, status",
            tenant_id="t1",
            name="orders",
            status="disabled",
        )
    )
    assert row == {"name": "orders", "status": "disabled"}
    update = pool.execute.await_args.args
    assert update[0] == "UPDATE object_source SET status = $1 WHERE tenant_id = $2 AND name = $3"
    assert update[1:] == ("disabled", "t1", "orders")
    assert "object_source" in pool.fetchrow.await_args.args[0]


def test_resolve_source_secret_maps_connector_safety_to_fetch_error(monkeypatch) -> None:
    def boom(ref, *, tenant_id):
        raise ConnectorSafetyError("tenant mismatch")

    monkeypatch.setattr(
        "app.source_registry_base.resolve_connector_secret",
        boom,
    )
    with pytest.raises(SourceFetchError, match="tenant mismatch"):
        resolve_source_secret("vault://x", tenant_id="t1")


def test_resolve_source_secret_passes_through(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.source_registry_base.resolve_connector_secret",
        lambda ref, *, tenant_id: f"{tenant_id}:{ref}",
    )
    assert resolve_source_secret("vault://s", tenant_id="acme") == "acme:vault://s"


def test_assert_dataset_available_skips_own_table() -> None:
    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=[None, None, None, None, None])

    asyncio.run(
        assert_dataset_available(
            pool,
            tenant_id="t1",
            name="orders",
            exclude_table="sql_source",
        )
    )

    assert pool.fetchval.await_count == 5
    peer_sql = [c.args[0] for c in pool.fetchval.await_args_list[1:]]
    assert all("FROM sql_source " not in q for q in peer_sql)
    assert any("generic_rest_source" in q for q in peer_sql)
    assert any("object_source" in q for q in peer_sql)


def test_assert_dataset_available_reserved() -> None:
    pool = MagicMock()
    pool.fetchval = AsyncMock()
    with pytest.raises(SourceConflictError, match="reserved"):
        asyncio.run(
            assert_dataset_available(
                pool,
                tenant_id="t1",
                name="system",
                reserved_dataset_names=frozenset({"system"}),
                exclude_table="sql_source",
            )
        )
    pool.fetchval.assert_not_awaited()


def test_assert_dataset_available_peer_conflict() -> None:
    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=[None, "orders"])

    with pytest.raises(SourceConflictError, match="REST source"):
        asyncio.run(
            assert_dataset_available(
                pool,
                tenant_id="t1",
                name="orders",
                exclude_table="sql_source",
            )
        )


def test_make_property_cursor_commit() -> None:
    pool = MagicMock()
    pool.execute = AsyncMock()
    commit = make_property_cursor_commit(
        pool,
        table="sql_source",
        tenant_id="t1",
        name="orders",
        cursor="2024-01-01",
        boundary_keys='["a"]',
    )
    asyncio.run(commit())
    pool.execute.assert_awaited_once()
    sql, cursor, boundary, tenant_id, name = pool.execute.await_args.args
    assert "sql_source" in sql
    assert "last_cursor_value" in sql
    assert cursor == "2024-01-01"
    assert boundary == '["a"]'
    assert tenant_id == "t1"
    assert name == "orders"


def test_make_column_cursor_commit_rejects_unknown_column() -> None:
    pool = MagicMock()
    with pytest.raises(ValueError, match="unknown cursor column"):
        make_column_cursor_commit(
            pool,
            table="object_source",
            tenant_id="t1",
            name="files",
            column="drop_me",
            value="x",
        )


def test_make_column_cursor_commit() -> None:
    pool = MagicMock()
    pool.execute = AsyncMock()
    commit = make_column_cursor_commit(
        pool,
        table="object_source",
        tenant_id="t1",
        name="files",
        column="last_synced_key",
        value="key/1",
    )
    asyncio.run(commit())
    sql, value, tenant_id, name = pool.execute.await_args.args
    assert "last_synced_key" in sql
    assert value == "key/1"
    assert tenant_id == "t1"
    assert name == "files"
