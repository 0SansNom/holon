"""Generic REST source registry — no-code REST connector registry.

Allows registering REST data sources, authentication headers, record extraction paths, and pagination config.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

import asyncpg

from app import source_registry_base
from app.source_registry_base import (
    ConnectionConflictError as ConnectionConflictError,
    ConnectionInUseError as ConnectionInUseError,
    SourceConflictError as SourceConflictError,
    SourceConfigError,
    SourceFetchError as SourceFetchError,
    assert_dataset_available,
)
from holon_common.connector_safety import (
    ConnectorSafetyError,
    assert_connector_secret_ref,
    assert_destination_change_requires_secret,
    assert_http_url,
    assert_no_inline_connector_secret,
    assert_production_requires_secret_ref,
    same_origin,
)


# Columns safe to return to caller (excludes raw credential values).
_PUBLIC_COLUMNS = (
    "tenant_id, name, workspace_id, base_url, auth_header_name, (auth_header_value IS NOT NULL) AS has_auth_header_value, "
    "record_path, next_page_path, connection_name, schedule_interval_minutes, "
    "cursor_property, incremental_param, last_cursor_value, status, created_by_urn, created_at"
)

_CONNECTION_PUBLIC_COLUMNS = (
    "tenant_id, name, auth_type, auth_header_name, (auth_header_value IS NOT NULL) AS has_auth_header_value, "
    "(secret_ref IS NOT NULL) AS has_secret_ref, "
    "oauth2_token_url, oauth2_client_id, (oauth2_client_secret IS NOT NULL) AS has_oauth2_client_secret, oauth2_scope, "
    "allowed_origin, created_by_urn, created_at"
)


_VALID_CONNECTION_AUTH_TYPES = frozenset({"header", "oauth2_client_credentials"})


def _normalize_origin(value: str) -> str:
    """`https://api.example.com/v1/x` → `https://api.example.com` (port kept)."""
    parsed = urlsplit((value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SourceConfigError(f"allowed_origin must be an http(s) origin, got {value!r}")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


def _assert_connection_origin(connection_name: str, allowed_origin: Optional[str], base_url: str) -> None:
    """A connection's credential only ever goes to the origin it was bound to.

    Sources carry the base_url, connections carry the secret: without this
    pin, any editor could point a new source at their own host and have
    Holon send a shared connection's header or bearer token there.
    """
    if not allowed_origin:
        raise SourceConfigError(
            f"connection {connection_name!r} has no allowed_origin — edit the connection to set it"
        )
    if not same_origin(allowed_origin, base_url):
        raise SourceConfigError(
            f"base_url origin does not match connection {connection_name!r} allowed_origin {allowed_origin!r}"
        )


async def register_connection(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    created_by_urn: str,
    auth_type: str = "header",
    auth_header_name: Optional[str] = None,
    auth_header_value: Optional[str] = None,
    oauth2_token_url: Optional[str] = None,
    oauth2_client_id: Optional[str] = None,
    oauth2_client_secret: Optional[str] = None,
    oauth2_scope: Optional[str] = None,
    secret_ref: Optional[str] = None,
    allowed_origin: Optional[str] = None,
) -> dict:
    """Register or update a REST connection credential."""
    if auth_type not in _VALID_CONNECTION_AUTH_TYPES:
        raise SourceConfigError(f"invalid auth_type: {auth_type!r} (must be one of {sorted(_VALID_CONNECTION_AUTH_TYPES)})")
    if auth_type == "header" and not auth_header_name:
        raise SourceConfigError("auth_type='header' requires auth_header_name")
    if auth_type == "oauth2_client_credentials" and not (oauth2_token_url and oauth2_client_id):
        raise SourceConfigError(
            "auth_type='oauth2_client_credentials' requires oauth2_token_url and oauth2_client_id"
        )

    existing = await pool.fetchrow(
        "SELECT auth_type, oauth2_token_url, oauth2_client_id, auth_header_value, oauth2_client_secret, secret_ref, "
        "allowed_origin FROM generic_rest_connection WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )
    is_update = existing is not None
    if allowed_origin is not None:
        allowed_origin = _normalize_origin(allowed_origin)
    elif existing is not None:
        allowed_origin = existing["allowed_origin"]
    if not allowed_origin:
        raise SourceConfigError("allowed_origin is required (e.g. 'https://api.example.com')")
    try:
        if oauth2_token_url:
            assert_http_url(oauth2_token_url, resolve=False)
        assert_http_url(allowed_origin, resolve=False)
        assert_connector_secret_ref(secret_ref, tenant_id=tenant_id)
        assert_no_inline_connector_secret(auth_header_value, field="auth_header_value")
        assert_no_inline_connector_secret(oauth2_client_secret, field="oauth2_client_secret")
        if existing is not None:
            destination_changed = (
                existing["auth_type"] != auth_type
                or (existing["allowed_origin"] or None) != allowed_origin
                or (existing["oauth2_token_url"] or None) != (oauth2_token_url or None)
                or (existing["oauth2_client_id"] or None) != (oauth2_client_id or None)
            )
            assert_destination_change_requires_secret(
                is_update=True,
                destination_changed=destination_changed,
                secret_provided=(
                    auth_header_value is not None
                    or oauth2_client_secret is not None
                    or secret_ref is not None
                ),
            )
    except ConnectorSafetyError as exc:
        raise SourceConfigError(str(exc)) from exc
    if auth_header_value is None and existing is not None:
        auth_header_value = existing["auth_header_value"]
    if oauth2_client_secret is None and existing is not None:
        oauth2_client_secret = existing["oauth2_client_secret"]
    if secret_ref is None and existing is not None:
        secret_ref = existing["secret_ref"]
    try:
        assert_production_requires_secret_ref(secret_ref, is_update=is_update)
    except ConnectorSafetyError as exc:
        raise SourceConfigError(str(exc)) from exc
    if auth_type == "oauth2_client_credentials" and not oauth2_client_secret and not secret_ref:
        raise SourceConfigError("auth_type='oauth2_client_credentials' requires oauth2_client_secret or secret_ref")

    await pool.execute(
        """
        INSERT INTO generic_rest_connection (
            tenant_id, name, auth_type, auth_header_name, auth_header_value,
            oauth2_token_url, oauth2_client_id, oauth2_client_secret, oauth2_scope, secret_ref, created_by_urn,
            allowed_origin
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            auth_type = EXCLUDED.auth_type,
            auth_header_name = EXCLUDED.auth_header_name,
            auth_header_value = EXCLUDED.auth_header_value,
            oauth2_token_url = EXCLUDED.oauth2_token_url,
            oauth2_client_id = EXCLUDED.oauth2_client_id,
            oauth2_client_secret = EXCLUDED.oauth2_client_secret,
            oauth2_scope = EXCLUDED.oauth2_scope,
            secret_ref = EXCLUDED.secret_ref,
            allowed_origin = EXCLUDED.allowed_origin,
            -- A re-registration changes credentials; the cached token
            -- from the old ones must not survive it.
            oauth2_cached_token = NULL,
            oauth2_token_expires_at = NULL
        """,
        tenant_id, name, auth_type, auth_header_name, auth_header_value,
        oauth2_token_url, oauth2_client_id, oauth2_client_secret, oauth2_scope, secret_ref, created_by_urn,
        allowed_origin,
    )
    return await get_connection(pool, tenant_id, name)


async def get_connection(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        f"SELECT {_CONNECTION_PUBLIC_COLUMNS} FROM generic_rest_connection WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )
    return None if row is None else dict(row)


async def list_connections(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        f"SELECT {_CONNECTION_PUBLIC_COLUMNS} FROM generic_rest_connection WHERE tenant_id = $1 ORDER BY name",
        tenant_id,
    )
    return [dict(row) for row in rows]


async def delete_connection(pool: asyncpg.Pool, tenant_id: str, name: str) -> None:
    await source_registry_base.delete_connection_if_unused(
        pool,
        connection_table="generic_rest_connection",
        source_table="generic_rest_source",
        tenant_id=tenant_id,
        name=name,
    )

async def register_source(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    base_url: str,
    created_by_urn: str,
    workspace_id: str,
    auth_header_name: Optional[str] = None,
    auth_header_value: Optional[str] = None,
    record_path: Optional[str] = None,
    next_page_path: Optional[str] = None,
    connection_name: Optional[str] = None,
    schedule_interval_minutes: Optional[int] = None,
    cursor_property: Optional[str] = None,
    incremental_param: Optional[str] = None,
    reserved_dataset_names: frozenset[str] = frozenset(),
) -> dict:
    """Verify dataset name availability and validate REST source parameters."""
    existing_source = await pool.fetchrow(
        "SELECT base_url, auth_header_value FROM generic_rest_source WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )
    try:
        assert_http_url(base_url, resolve=False)
        assert_no_inline_connector_secret(auth_header_value, field="auth_header_value")
        if existing_source is not None and not connection_name:
            # Inline-auth sources: moving base_url to another origin must re-supply
            # the header secret (path/query edits on the same origin keep it).
            assert_destination_change_requires_secret(
                is_update=True,
                destination_changed=not same_origin(existing_source["base_url"], base_url),
                secret_provided=auth_header_value is not None,
            )
    except ConnectorSafetyError as exc:
        raise SourceConfigError(str(exc)) from exc
    if connection_name and (auth_header_name or auth_header_value):
        raise SourceConfigError(
            "a source can use a named connection or its own inline auth header, not both — "
            "clear one before setting the other"
        )
    if connection_name:
        connection = await get_connection(pool, tenant_id, connection_name)
        if connection is None:
            raise SourceConfigError(f"unknown connection: {connection_name!r}")
        _assert_connection_origin(connection_name, connection["allowed_origin"], base_url)
    if schedule_interval_minutes is not None and schedule_interval_minutes <= 0:
        raise SourceConfigError("schedule_interval_minutes must be a positive number of minutes")

    await assert_dataset_available(
        pool,
        tenant_id=tenant_id,
        name=name,
        reserved_dataset_names=reserved_dataset_names,
        exclude_table="generic_rest_source",
    )

    await pool.execute(
        """
        INSERT INTO generic_rest_source
            (tenant_id, name, workspace_id, base_url, auth_header_name, auth_header_value, record_path, next_page_path,
             connection_name, schedule_interval_minutes, cursor_property, incremental_param, status, created_by_urn)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'active', $13)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            workspace_id = EXCLUDED.workspace_id,
            base_url = EXCLUDED.base_url,
            auth_header_name = EXCLUDED.auth_header_name,
            -- Retain existing secret if omitted on edit.
            auth_header_value = COALESCE(EXCLUDED.auth_header_value, generic_rest_source.auth_header_value),
            record_path = EXCLUDED.record_path,
            next_page_path = EXCLUDED.next_page_path,
            connection_name = EXCLUDED.connection_name,
            schedule_interval_minutes = EXCLUDED.schedule_interval_minutes,
            cursor_property = EXCLUDED.cursor_property,
            incremental_param = EXCLUDED.incremental_param,
            -- Preserve system-managed last_cursor_value during edit.
            status = 'active'
        """,
        tenant_id, name, workspace_id, base_url, auth_header_name, auth_header_value, record_path, next_page_path,
        connection_name, schedule_interval_minutes, cursor_property, incremental_param, created_by_urn,
    )
    return await get_source(pool, tenant_id, name)


async def list_scheduled_sources(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    return await source_registry_base.list_scheduled_sources_rows(pool, table="generic_rest_source", tenant_id=tenant_id)

async def list_all_scheduled_sources(pool: asyncpg.Pool) -> list[dict]:
    return await source_registry_base.list_all_scheduled_sources_rows(pool, table="generic_rest_source")

async def set_source_status(pool: asyncpg.Pool, tenant_id: str, name: str, status: str) -> Optional[dict]:
    return await source_registry_base.set_source_status_row(
        pool, table="generic_rest_source", public_columns=_PUBLIC_COLUMNS, tenant_id=tenant_id, name=name, status=status
    )

async def delete_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> None:
    await source_registry_base.delete_source_row(pool, table="generic_rest_source", tenant_id=tenant_id, name=name)

async def get_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    return await source_registry_base.get_source_row(
        pool, table="generic_rest_source", public_columns=_PUBLIC_COLUMNS, tenant_id=tenant_id, name=name
    )

async def list_sources(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    return await source_registry_base.list_sources_for_tenant(
        pool, table="generic_rest_source", public_columns=_PUBLIC_COLUMNS, tenant_id=tenant_id
    )

async def is_registered(pool: asyncpg.Pool, tenant_id: str, name: str) -> bool:
    return await source_registry_base.is_source_active(pool, table="generic_rest_source", tenant_id=tenant_id, name=name)

from app.generic_rest_fetch import (  # noqa: E402,F401
    _add_query_param,
    _extract_next_url,
    _extract_records,
    _oauth2_bearer_token,
    fetch_for_dataset,
)
