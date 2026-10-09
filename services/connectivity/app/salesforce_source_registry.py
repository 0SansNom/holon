"""No-code Salesforce source registry — Connected App client credentials + SOQL.

Auth mirrors CData service-account style: client_id + client_secret
(or secret_ref) against {login_url}/services/oauth2/token. The token response
instance_url is cached on the connection and used for subsequent query calls.
"""

from __future__ import annotations

import datetime
import re
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

import asyncpg
import httpx

from app.cursor_window import advance_cursor
from app.pinned_http import pinned_transport
from app import source_registry_base
from app.source_registry_base import (
    ConnectionInUseError,
    SourceConflictError,
    SourceConfigError,
    SourceFetchError,
    assert_dataset_available,
    make_property_cursor_commit,
    resolve_source_secret,
)
from holon_common.connector_safety import (
    ConnectorSafetyError,
    assert_connector_secret_ref,
    assert_destination_change_requires_secret,
    assert_http_url,
    assert_no_inline_connector_secret,
    assert_production_requires_secret_ref,
    connector_secret,
    same_origin,
)

_DEFAULT_LOGIN_URL = "https://login.salesforce.com"
_DEFAULT_API_VERSION = "v59.0"
_API_VERSION_RE = re.compile(r"^v\d+(?:\.\d+)?$")
_CURSOR_PROPERTY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
# ISO-8601 date or datetime, which SOQL requires as an unquoted Date/DateTime
# literal on Date/DateTime fields.

_PUBLIC_CONNECTION_COLUMNS = (
    "tenant_id, name, login_url, client_id, "
    "(client_secret IS NOT NULL OR secret_ref IS NOT NULL) AS has_client_secret, "
    "instance_url, created_by_urn, created_at"
)

_PUBLIC_SOURCE_COLUMNS = (
    "tenant_id, name, workspace_id, connection_name, soql, api_version, "
    "cursor_property, last_cursor_value, schedule_interval_minutes, status, "
    "created_by_urn, created_at"
)


def _normalize_login_url(login_url: str) -> str:
    url = (login_url or _DEFAULT_LOGIN_URL).strip().rstrip("/")
    try:
        assert_http_url(url)
    except ConnectorSafetyError as exc:
        raise SourceConfigError(str(exc)) from exc
    return url


def _normalize_api_version(api_version: str) -> str:
    version = (api_version or _DEFAULT_API_VERSION).strip()
    if not _API_VERSION_RE.match(version):
        raise SourceConfigError(
            f"api_version must look like 'v59.0', got {api_version!r}"
        )
    return version


def _require_soql(soql: str) -> str:
    cleaned = (soql or "").strip().rstrip(";").strip()
    if not cleaned:
        raise SourceConfigError("soql is required")
    if ";" in cleaned:
        raise SourceConfigError("soql must be a single SELECT statement — no semicolons")
    if not cleaned.lower().startswith("select"):
        raise SourceConfigError("soql must be a SELECT statement")
    return cleaned


def _require_cursor_property(cursor_property: Optional[str]) -> Optional[str]:
    if cursor_property is None or not str(cursor_property).strip():
        return None
    name = str(cursor_property).strip()
    if not _CURSOR_PROPERTY_RE.match(name):
        raise SourceConfigError(
            f"invalid cursor_property {cursor_property!r} — use a Salesforce field API name"
        )
    return name


async def register_connection(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    client_id: str,
    created_by_urn: str,
    login_url: Optional[str] = None,
    client_secret: Optional[str] = None,
    secret_ref: Optional[str] = None,
) -> dict:
    """Register or update a Salesforce Connected App credential."""
    if not client_id.strip():
        raise SourceConfigError("client_id is required")
    login = _normalize_login_url(login_url or _DEFAULT_LOGIN_URL)
    existing = await pool.fetchrow(
        "SELECT login_url, client_id, client_secret, secret_ref FROM salesforce_connection "
        "WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )
    is_update = existing is not None
    try:
        assert_connector_secret_ref(secret_ref, tenant_id=tenant_id)
        assert_no_inline_connector_secret(client_secret, field="client_secret")
        if existing is not None:
            destination_changed = (
                existing["login_url"] != login
                or existing["client_id"] != client_id
            )
            assert_destination_change_requires_secret(
                is_update=True,
                destination_changed=destination_changed,
                secret_provided=client_secret is not None or secret_ref is not None,
            )
    except ConnectorSafetyError as exc:
        raise SourceConfigError(str(exc)) from exc
    if client_secret is None and secret_ref is None and existing is not None:
        client_secret, secret_ref = existing["client_secret"], existing["secret_ref"]
    try:
        assert_production_requires_secret_ref(secret_ref, is_update=is_update)
    except ConnectorSafetyError as exc:
        raise SourceConfigError(str(exc)) from exc

    await pool.execute(
        """
        INSERT INTO salesforce_connection
            (tenant_id, name, login_url, client_id, client_secret, secret_ref, created_by_urn)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            login_url = EXCLUDED.login_url,
            client_id = EXCLUDED.client_id,
            client_secret = EXCLUDED.client_secret,
            secret_ref = EXCLUDED.secret_ref,
            oauth2_cached_token = NULL,
            oauth2_token_expires_at = NULL,
            instance_url = NULL
        """,
        tenant_id, name, login, client_id.strip(), client_secret, secret_ref, created_by_urn,
    )
    return await get_connection(pool, tenant_id, name)


async def get_connection(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        f"SELECT {_PUBLIC_CONNECTION_COLUMNS} FROM salesforce_connection "
        "WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )
    return None if row is None else dict(row)


async def list_connections(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        f"SELECT {_PUBLIC_CONNECTION_COLUMNS} FROM salesforce_connection "
        "WHERE tenant_id = $1 ORDER BY name",
        tenant_id,
    )
    return [dict(row) for row in rows]


async def delete_connection(pool: asyncpg.Pool, tenant_id: str, name: str) -> None:
    await source_registry_base.delete_connection_if_unused(
        pool, connection_table="salesforce_connection", source_table="salesforce_source", tenant_id=tenant_id, name=name
    )

async def register_source(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    workspace_id: str,
    connection_name: str,
    soql: str,
    created_by_urn: str,
    api_version: str = _DEFAULT_API_VERSION,
    cursor_property: Optional[str] = None,
    schedule_interval_minutes: Optional[int] = None,
    reserved_dataset_names: frozenset[str] = frozenset(),
) -> dict:
    """Verify dataset name availability and validate Salesforce source parameters."""
    soql = _require_soql(soql)
    api_version = _normalize_api_version(api_version)
    cursor_property = _require_cursor_property(cursor_property)
    if await get_connection(pool, tenant_id, connection_name) is None:
        raise SourceConfigError(f"unknown connection: {connection_name!r}")
    if schedule_interval_minutes is not None and schedule_interval_minutes <= 0:
        raise SourceConfigError("schedule_interval_minutes must be a positive number of minutes")

    await assert_dataset_available(
        pool,
        tenant_id=tenant_id,
        name=name,
        reserved_dataset_names=reserved_dataset_names,
        exclude_table="salesforce_source",
    )

    await pool.execute(
        """
        INSERT INTO salesforce_source
            (tenant_id, name, workspace_id, connection_name, soql, api_version,
             cursor_property, schedule_interval_minutes, status, created_by_urn)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'active', $9)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            workspace_id = EXCLUDED.workspace_id,
            connection_name = EXCLUDED.connection_name,
            soql = EXCLUDED.soql,
            api_version = EXCLUDED.api_version,
            cursor_property = EXCLUDED.cursor_property,
            schedule_interval_minutes = EXCLUDED.schedule_interval_minutes,
            status = 'active'
        """,
        tenant_id, name, workspace_id, connection_name, soql, api_version,
        cursor_property, schedule_interval_minutes, created_by_urn,
    )
    return await get_source(pool, tenant_id, name)


async def list_scheduled_sources(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    return await source_registry_base.list_scheduled_sources_rows(pool, table="salesforce_source", tenant_id=tenant_id)

async def list_all_scheduled_sources(pool: asyncpg.Pool) -> list[dict]:
    return await source_registry_base.list_all_scheduled_sources_rows(pool, table="salesforce_source")

async def set_source_status(pool: asyncpg.Pool, tenant_id: str, name: str, status: str) -> Optional[dict]:
    return await source_registry_base.set_source_status_row(
        pool, table="salesforce_source", public_columns=_PUBLIC_SOURCE_COLUMNS, tenant_id=tenant_id, name=name, status=status
    )

async def delete_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> None:
    await source_registry_base.delete_source_row(pool, table="salesforce_source", tenant_id=tenant_id, name=name)

async def get_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    return await source_registry_base.get_source_row(
        pool, table="salesforce_source", public_columns=_PUBLIC_SOURCE_COLUMNS, tenant_id=tenant_id, name=name
    )

async def list_sources(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    return await source_registry_base.list_sources_for_tenant(
        pool, table="salesforce_source", public_columns=_PUBLIC_SOURCE_COLUMNS, tenant_id=tenant_id
    )

async def is_registered(pool: asyncpg.Pool, tenant_id: str, name: str) -> bool:
    return await source_registry_base.is_source_active(pool, table="salesforce_source", tenant_id=tenant_id, name=name)

from app.salesforce_source_fetch import (  # noqa: E402,F401
    _apply_cursor,
    _bearer_token,
    _next_page_url,
    _parse_iso_cursor,
    _soql_date_literal,
    _strip_attributes,
    fetch_for_dataset,
)
