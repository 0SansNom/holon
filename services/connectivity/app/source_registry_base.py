"""Shared glue for no-code source registries.

Connectors keep connect / list / read. Conflict checks, secret resolve at
fetch time, and deferred cursor commits after Iceberg write live here so
a fix is not applied five times.
"""

from __future__ import annotations

import re
from typing import Awaitable, Callable, Optional

import asyncpg

from holon_common.connector_safety import ConnectorSafetyError, resolve_connector_secret

CommitCursor = Callable[[], Awaitable[None]]

# Whitelisted peer tables and the label used in conflict messages.
SOURCE_DATASET_TABLES: tuple[tuple[str, str], ...] = (
    ("generic_rest_source", "REST source"),
    ("sql_source", "SQL source"),
    ("object_source", "object source"),
    ("sftp_source", "SFTP source"),
    ("salesforce_source", "Salesforce source"),
)

_ALLOWED_SOURCE_TABLES = frozenset(table for table, _ in SOURCE_DATASET_TABLES)
_ALLOWED_CURSOR_COLUMNS = frozenset(
    {"last_cursor_value", "last_synced_key", "last_synced_path", "cursor_boundary_keys"}
)
# Public projections are identifier lists plus `(col IS NOT NULL) AS alias`.
_COLUMN_LIST = re.compile(r"^[A-Za-z0-9_(),\s]+$")


class SourceConflictError(ValueError):
    pass


class SourceConfigError(ValueError):
    pass


class SourceFetchError(ValueError):
    pass


class ConnectionInUseError(ValueError):
    pass


class ConnectionConflictError(ValueError):
    pass


def _require_source_table(table: str) -> None:
    if table not in _ALLOWED_SOURCE_TABLES:
        raise ValueError(f"unknown source table {table!r}")


def _require_column_list(columns: str) -> None:
    if not columns or _COLUMN_LIST.fullmatch(columns) is None:
        raise ValueError("invalid source column list")


async def get_row(
    pool: asyncpg.Pool, *, table: str, columns: str, tenant_id: str, name: str
) -> Optional[dict]:
    _require_source_table(table)
    _require_column_list(columns)
    row = await pool.fetchrow(
        f"SELECT {columns} FROM {table} WHERE tenant_id = $1 AND name = $2",
        tenant_id,
        name,
    )
    return None if row is None else dict(row)


async def list_rows(pool: asyncpg.Pool, *, table: str, columns: str, tenant_id: str) -> list[dict]:
    _require_source_table(table)
    _require_column_list(columns)
    rows = await pool.fetch(
        f"SELECT {columns} FROM {table} WHERE tenant_id = $1 ORDER BY name",
        tenant_id,
    )
    return [dict(row) for row in rows]


async def delete_row(pool: asyncpg.Pool, *, table: str, tenant_id: str, name: str) -> None:
    _require_source_table(table)
    await pool.execute(
        f"DELETE FROM {table} WHERE tenant_id = $1 AND name = $2",
        tenant_id,
        name,
    )


async def set_status(
    pool: asyncpg.Pool, *, table: str, columns: str, tenant_id: str, name: str, status: str
) -> Optional[dict]:
    _require_source_table(table)
    _require_column_list(columns)
    await pool.execute(
        f"UPDATE {table} SET status = $1 WHERE tenant_id = $2 AND name = $3",
        status,
        tenant_id,
        name,
    )
    return await get_row(pool, table=table, columns=columns, tenant_id=tenant_id, name=name)


async def is_registered(pool: asyncpg.Pool, *, table: str, tenant_id: str, name: str) -> bool:
    _require_source_table(table)
    return await pool.fetchval(
        f"SELECT true FROM {table} WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id,
        name,
    ) or False


def resolve_source_secret(ref: Optional[str], *, tenant_id: str) -> Optional[str]:
    """Resolve a stored secret_ref, re-checking tenant scope at fetch time."""
    try:
        return resolve_connector_secret(ref, tenant_id=tenant_id)
    except ConnectorSafetyError as exc:
        raise SourceFetchError(str(exc)) from exc


async def assert_dataset_available(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    reserved_dataset_names: frozenset[str] = frozenset(),
    exclude_table: Optional[str] = None,
) -> None:
    """Refuse a dataset name already reserved, owned by a plugin, or another source.

    ``exclude_table`` is the caller's own ``*_source`` table so re-register
    via ``ON CONFLICT`` does not collide with itself.
    """
    if exclude_table is not None and exclude_table not in _ALLOWED_SOURCE_TABLES:
        raise ValueError(f"unknown source table {exclude_table!r}")

    if name in reserved_dataset_names:
        raise SourceConflictError(f"dataset {name!r} is reserved")

    conflicting_plugin = await pool.fetchval(
        """
        SELECT name FROM plugin_registration
        WHERE manifest->>'dataset_name' = $1
          AND status = 'active'
          AND (tenant_id IS NULL OR tenant_id = $2)
        """,
        name,
        tenant_id,
    )
    if conflicting_plugin is not None:
        raise SourceConflictError(
            f"dataset {name!r} is already claimed by active plugin {conflicting_plugin!r}"
        )

    for table, label in SOURCE_DATASET_TABLES:
        if table == exclude_table:
            continue
        conflicting = await pool.fetchval(
            f"SELECT name FROM {table} WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
            tenant_id,
            name,
        )
        if conflicting is not None:
            raise SourceConflictError(
                f"dataset {name!r} is already claimed by active {label} {conflicting!r}"
            )


def make_property_cursor_commit(
    pool: asyncpg.Pool,
    *,
    table: str,
    tenant_id: str,
    name: str,
    cursor: str,
    boundary_keys: str,
) -> CommitCursor:
    """Persist inclusive property-cursor resume after Iceberg write succeeds."""
    if table not in _ALLOWED_SOURCE_TABLES:
        raise ValueError(f"unknown source table {table!r}")

    async def _commit(
        cursor_value: str = cursor,
        boundary: str = boundary_keys,
    ) -> None:
        await pool.execute(
            f"UPDATE {table} SET last_cursor_value = $1, cursor_boundary_keys = $2 "
            "WHERE tenant_id = $3 AND name = $4",
            cursor_value,
            boundary,
            tenant_id,
            name,
        )

    return _commit


def make_column_cursor_commit(
    pool: asyncpg.Pool,
    *,
    table: str,
    tenant_id: str,
    name: str,
    column: str,
    value: str,
) -> CommitCursor:
    """Persist a single resume column after Iceberg write succeeds."""
    if table not in _ALLOWED_SOURCE_TABLES:
        raise ValueError(f"unknown source table {table!r}")
    if column not in _ALLOWED_CURSOR_COLUMNS:
        raise ValueError(f"unknown cursor column {column!r}")

    async def _commit(cursor_value: str = value) -> None:
        await pool.execute(
            f"UPDATE {table} SET {column} = $1 WHERE tenant_id = $2 AND name = $3",
            cursor_value,
            tenant_id,
            name,
        )

    return _commit
