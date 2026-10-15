"""No-code SQL source registry for Postgres, MySQL/MariaDB, SQL Server, and Snowflake."""

from __future__ import annotations

from typing import Optional

import asyncpg

from holon_common.connector_safety import (
    ConnectorSafetyError,
    assert_connector_host,
    assert_connector_secret_ref,
    assert_destination_change_requires_secret,
    assert_no_inline_connector_secret,
    assert_production_requires_secret_ref,
)

from app import source_registry_base, sql_drivers
from app.source_registry_base import (
    ConnectionConflictError as ConnectionConflictError,
    ConnectionInUseError as ConnectionInUseError,
    SourceConflictError as SourceConflictError,
    SourceConfigError,
    SourceFetchError as SourceFetchError,
    assert_dataset_available,
)
from app.sql_source_validation import _require_select_only
from holon_common.sql_ident import require_identifier

_PUBLIC_CONNECTION_COLUMNS = (
    "tenant_id, name, dialect, host, port, database, warehouse, username, use_tls, "
    "(password IS NOT NULL OR secret_ref IS NOT NULL) AS has_password, "
    "created_by_urn, created_at"
)

_PUBLIC_SOURCE_COLUMNS = (
    "tenant_id, name, workspace_id, connection_name, table_name, query, schedule_interval_minutes, "
    "cursor_property, last_cursor_value, status, created_by_urn, created_at"
)

async def register_connection(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    host: str,
    database: str,
    username: str,
    created_by_urn: str,
    dialect: str = "postgres",
    port: Optional[int] = None,
    warehouse: Optional[str] = None,
    password: Optional[str] = None,
    secret_ref: Optional[str] = None,
    use_tls: Optional[bool] = None,
) -> dict:
    """Register or update a SQL connection credential."""
    try:
        dialect = sql_drivers.normalize_dialect(dialect)
        use_tls = sql_drivers.resolve_use_tls(dialect, use_tls)
    except ValueError as exc:
        raise SourceConfigError(str(exc)) from exc
    if sql_drivers.wire_dialect(dialect) == "snowflake":
        try:
            host = sql_drivers.normalize_snowflake_host(host)
        except ValueError as exc:
            raise SourceConfigError(str(exc)) from exc
        if warehouse is not None:
            warehouse = warehouse.strip() or None
    else:
        warehouse = None
    if port is None:
        port = sql_drivers.default_port_for(dialect)

    existing = await pool.fetchrow(
        "SELECT host, port, dialect, database, username, password, secret_ref, use_tls "
        "FROM sql_connection WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )
    is_update = existing is not None
    try:
        assert_connector_host(host)
        assert_connector_secret_ref(secret_ref, tenant_id=tenant_id)
        assert_no_inline_connector_secret(password, field="password")
        if existing is not None:
            destination_changed = (
                existing["host"] != host
                or int(existing["port"]) != int(port)
                or (existing["dialect"] or "postgres") != dialect
                or existing["database"] != database
                or bool(existing["use_tls"]) != use_tls
            )
            assert_destination_change_requires_secret(
                is_update=True,
                destination_changed=destination_changed,
                secret_provided=password is not None or secret_ref is not None,
            )
    except ConnectorSafetyError as exc:
        raise SourceConfigError(str(exc)) from exc
    if password is None and secret_ref is None and existing is not None:
        password, secret_ref = existing["password"], existing["secret_ref"]
    try:
        assert_production_requires_secret_ref(secret_ref, is_update=is_update)
    except ConnectorSafetyError as exc:
        raise SourceConfigError(str(exc)) from exc

    await pool.execute(
        """
        INSERT INTO sql_connection
            (tenant_id, name, dialect, host, port, database, warehouse, username, password, secret_ref, use_tls, created_by_urn)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            dialect = EXCLUDED.dialect,
            host = EXCLUDED.host,
            port = EXCLUDED.port,
            database = EXCLUDED.database,
            warehouse = EXCLUDED.warehouse,
            username = EXCLUDED.username,
            password = EXCLUDED.password,
            secret_ref = EXCLUDED.secret_ref,
            use_tls = EXCLUDED.use_tls
        """,
        tenant_id, name, dialect, host, port, database, warehouse, username, password, secret_ref, use_tls, created_by_urn,
    )
    return await get_connection(pool, tenant_id, name)

async def get_connection(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        f"SELECT {_PUBLIC_CONNECTION_COLUMNS} FROM sql_connection WHERE tenant_id = $1 AND name = $2", tenant_id, name
    )
    return None if row is None else dict(row)

async def list_connections(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        f"SELECT {_PUBLIC_CONNECTION_COLUMNS} FROM sql_connection WHERE tenant_id = $1 ORDER BY name", tenant_id
    )
    return [dict(row) for row in rows]

async def delete_connection(pool: asyncpg.Pool, tenant_id: str, name: str) -> None:
    await source_registry_base.delete_connection_if_unused(
        pool,
        connection_table="sql_connection",
        source_table="sql_source",
        tenant_id=tenant_id,
        name=name,
    )

async def register_source(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    workspace_id: str,
    connection_name: str,
    created_by_urn: str,
    table_name: Optional[str] = None,
    query: Optional[str] = None,
    schedule_interval_minutes: Optional[int] = None,
    cursor_property: Optional[str] = None,
    reserved_dataset_names: frozenset[str] = frozenset(),
) -> dict:
    """Verify dataset name availability and validate SQL source parameters."""
    if bool(table_name) == bool(query):
        raise SourceConfigError("exactly one of table_name or query must be set")
    if table_name:
        try:
            require_identifier(table_name, what="table_name")
        except ValueError as exc:
            raise SourceConfigError(str(exc)) from exc
    if cursor_property:
        try:
            require_identifier(cursor_property, what="cursor_property")
        except ValueError as exc:
            raise SourceConfigError(str(exc)) from exc

    connection = await get_connection(pool, tenant_id, connection_name)
    if connection is None:
        raise SourceConfigError(f"unknown connection: {connection_name!r}")
    dialect = connection.get("dialect") or "postgres"
    if query:
        _require_select_only(query, dialect)
    if schedule_interval_minutes is not None and schedule_interval_minutes <= 0:
        raise SourceConfigError("schedule_interval_minutes must be a positive number of minutes")

    await assert_dataset_available(
        pool,
        tenant_id=tenant_id,
        name=name,
        reserved_dataset_names=reserved_dataset_names,
        exclude_table="sql_source",
    )

    await pool.execute(
        """
        INSERT INTO sql_source
            (tenant_id, name, workspace_id, connection_name, table_name, query,
             schedule_interval_minutes, cursor_property, status, created_by_urn)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'active', $9)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            workspace_id = EXCLUDED.workspace_id,
            connection_name = EXCLUDED.connection_name,
            table_name = EXCLUDED.table_name,
            query = EXCLUDED.query,
            schedule_interval_minutes = EXCLUDED.schedule_interval_minutes,
            cursor_property = EXCLUDED.cursor_property,
            -- last_cursor_value deliberately absent: resume state
            -- computed by fetch_for_dataset, not a form field.
            status = 'active'
        """,
        tenant_id, name, workspace_id, connection_name, table_name, query,
        schedule_interval_minutes, cursor_property, created_by_urn,
    )
    return await get_source(pool, tenant_id, name)

async def list_scheduled_sources(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    return await source_registry_base.list_scheduled_sources_rows(pool, table="sql_source", tenant_id=tenant_id)

async def list_all_scheduled_sources(pool: asyncpg.Pool) -> list[dict]:
    return await source_registry_base.list_all_scheduled_sources_rows(pool, table="sql_source")

async def set_source_status(pool: asyncpg.Pool, tenant_id: str, name: str, status: str) -> Optional[dict]:
    return await source_registry_base.set_source_status_row(
        pool, table="sql_source", public_columns=_PUBLIC_SOURCE_COLUMNS, tenant_id=tenant_id, name=name, status=status
    )

async def delete_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> None:
    await source_registry_base.delete_source_row(pool, table="sql_source", tenant_id=tenant_id, name=name)

async def get_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    return await source_registry_base.get_source_row(
        pool, table="sql_source", public_columns=_PUBLIC_SOURCE_COLUMNS, tenant_id=tenant_id, name=name
    )

async def list_sources(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    return await source_registry_base.list_sources_for_tenant(
        pool, table="sql_source", public_columns=_PUBLIC_SOURCE_COLUMNS, tenant_id=tenant_id
    )

async def is_registered(pool: asyncpg.Pool, tenant_id: str, name: str) -> bool:
    return await source_registry_base.is_source_active(pool, table="sql_source", tenant_id=tenant_id, name=name)


from app.sql_source_fetch import (  # noqa: E402,F401
    _bind_cursor_value,
    _cursor_to_str,
    _parse_iso_cursor,
    _row_get,
    fetch_for_dataset,
)
