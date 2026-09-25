"""No-code Salesforce source registry — Connected App client credentials + SOQL.

Auth mirrors Foundry/CData service-account style: client_id + client_secret
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

from holon_common.connector_safety import (
    ConnectorSafetyError,
    assert_connector_secret_ref,
    assert_http_url,
    assert_no_inline_connector_secret,
    assert_production_requires_secret_ref,
    same_origin,
)
from holon_common.secrets import resolve_optional

_DEFAULT_LOGIN_URL = "https://login.salesforce.com"
_DEFAULT_API_VERSION = "v59.0"
_API_VERSION_RE = re.compile(r"^v\d+(?:\.\d+)?$")
_CURSOR_PROPERTY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_MAX_PAGES = 100
_OAUTH2_REFRESH_MARGIN_SECONDS = 60
_OAUTH2_DEFAULT_TTL_SECONDS = 300

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


class SourceConflictError(ValueError):
    pass


class SourceConfigError(ValueError):
    pass


class SourceFetchError(ValueError):
    pass


class ConnectionInUseError(ValueError):
    pass


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


def _apply_cursor(soql: str, cursor_property: str, last_cursor_value: str) -> str:
    """Append an incremental filter without rewriting the user's SELECT list."""
    literal = last_cursor_value.replace("\\", "\\\\").replace("'", "\\'")
    clause = f"{cursor_property} > '{literal}'"
    lowered = soql.lower()
    # Insert before ORDER BY / LIMIT / OFFSET when present.
    for keyword in (" order by ", " limit ", " offset "):
        idx = lowered.find(keyword)
        if idx != -1:
            head, tail = soql[:idx], soql[idx:]
            joiner = " AND " if " where " in head.lower() else " WHERE "
            return f"{head}{joiner}{clause}{tail}"
    joiner = " AND " if " where " in lowered else " WHERE "
    return f"{soql}{joiner}{clause}"


def _strip_attributes(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "attributes"}


def _coerce_cursor(value: Any) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


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
        "SELECT client_secret, secret_ref FROM salesforce_connection "
        "WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )
    is_update = existing is not None
    try:
        assert_connector_secret_ref(secret_ref, tenant_id=tenant_id)
        assert_no_inline_connector_secret(client_secret, field="client_secret")
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
    in_use = await pool.fetch(
        "SELECT name FROM salesforce_source WHERE tenant_id = $1 AND connection_name = $2",
        tenant_id, name,
    )
    if in_use:
        source_names = [row["name"] for row in in_use]
        raise ConnectionInUseError(
            f"connection {name!r} is still used by source(s) {source_names} — "
            "repoint or delete them first"
        )
    await pool.execute(
        "DELETE FROM salesforce_connection WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )


async def _assert_dataset_available(
    pool: asyncpg.Pool, *, tenant_id: str, name: str, reserved_dataset_names: frozenset[str]
) -> None:
    if name in reserved_dataset_names:
        raise SourceConflictError(f"dataset {name!r} is reserved")

    conflicting_plugin = await pool.fetchval(
        """
        SELECT name FROM plugin_registration
        WHERE manifest->>'dataset_name' = $1
          AND status = 'active'
          AND (tenant_id IS NULL OR tenant_id = $2)
        """,
        name, tenant_id,
    )
    if conflicting_plugin is not None:
        raise SourceConflictError(
            f"dataset {name!r} is already claimed by active plugin {conflicting_plugin!r}"
        )

    # Do not conflict-check salesforce_source itself — re-register updates via ON CONFLICT.
    for table, label in (
        ("generic_rest_source", "REST source"),
        ("sql_source", "SQL source"),
        ("object_source", "object source"),
        ("sftp_source", "SFTP source"),
    ):
        conflicting = await pool.fetchval(
            f"SELECT name FROM {table} WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
            tenant_id, name,
        )
        if conflicting is not None:
            raise SourceConflictError(
                f"dataset {name!r} is already claimed by active {label} {conflicting!r}"
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

    await _assert_dataset_available(
        pool, tenant_id=tenant_id, name=name, reserved_dataset_names=reserved_dataset_names
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
    rows = await pool.fetch(
        "SELECT name, schedule_interval_minutes FROM salesforce_source "
        "WHERE tenant_id = $1 AND status = 'active' AND schedule_interval_minutes IS NOT NULL",
        tenant_id,
    )
    return [dict(row) for row in rows]


async def list_all_scheduled_sources(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch(
        "SELECT tenant_id, name, workspace_id, schedule_interval_minutes FROM salesforce_source "
        "WHERE status = 'active' AND schedule_interval_minutes IS NOT NULL"
    )
    return [dict(row) for row in rows]


async def set_source_status(pool: asyncpg.Pool, tenant_id: str, name: str, status: str) -> Optional[dict]:
    await pool.execute(
        "UPDATE salesforce_source SET status = $1 WHERE tenant_id = $2 AND name = $3",
        status, tenant_id, name,
    )
    return await get_source(pool, tenant_id, name)


async def delete_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> None:
    await pool.execute(
        "DELETE FROM salesforce_source WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )


async def get_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        f"SELECT {_PUBLIC_SOURCE_COLUMNS} FROM salesforce_source "
        "WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )
    return None if row is None else dict(row)


async def list_sources(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        f"SELECT {_PUBLIC_SOURCE_COLUMNS} FROM salesforce_source "
        "WHERE tenant_id = $1 ORDER BY name",
        tenant_id,
    )
    return [dict(row) for row in rows]


async def is_registered(pool: asyncpg.Pool, tenant_id: str, name: str) -> bool:
    return await pool.fetchval(
        "SELECT true FROM salesforce_source "
        "WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    ) or False


async def _bearer_token(
    pool: asyncpg.Pool, tenant_id: str, connection_name: str, connection: asyncpg.Record
) -> tuple[str, str]:
    """Return (access_token, instance_url), refreshing when the cache is stale."""
    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = connection["oauth2_token_expires_at"]
    if (
        connection["oauth2_cached_token"]
        and connection["instance_url"]
        and expires_at is not None
        and (expires_at - now).total_seconds() > _OAUTH2_REFRESH_MARGIN_SECONDS
    ):
        return connection["oauth2_cached_token"], connection["instance_url"]

    client_secret = resolve_optional(connection["secret_ref"]) or connection["client_secret"]
    if not client_secret:
        raise SourceFetchError(
            f"connection {connection_name!r}: client_secret (or secret_ref) is required"
        )
    token_url = f"{connection['login_url'].rstrip('/')}/services/oauth2/token"
    try:
        assert_http_url(token_url)
    except ConnectorSafetyError as exc:
        raise SourceFetchError(str(exc)) from exc

    form = {
        "grant_type": "client_credentials",
        "client_id": connection["client_id"],
        "client_secret": client_secret,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(token_url, data=form)
    if response.status_code >= 400:
        raise SourceFetchError(
            f"connection {connection_name!r}: Salesforce token request failed "
            f"({response.status_code}): {response.text[:300]}"
        )
    body = response.json()
    token = body.get("access_token")
    instance_url = body.get("instance_url")
    if not token or not instance_url:
        raise SourceFetchError(
            f"connection {connection_name!r}: token response missing access_token or instance_url"
        )
    try:
        assert_http_url(instance_url)
    except ConnectorSafetyError as exc:
        raise SourceFetchError(str(exc)) from exc

    ttl_seconds = body.get("expires_in") or _OAUTH2_DEFAULT_TTL_SECONDS
    new_expires_at = now + datetime.timedelta(seconds=int(ttl_seconds))
    await pool.execute(
        "UPDATE salesforce_connection SET oauth2_cached_token = $1, oauth2_token_expires_at = $2, "
        "instance_url = $3 WHERE tenant_id = $4 AND name = $5",
        token, new_expires_at, instance_url.rstrip("/"), tenant_id, connection_name,
    )
    return token, instance_url.rstrip("/")


def _next_page_url(*, instance_url: str, next_records_url: Optional[str]) -> Optional[str]:
    if not next_records_url:
        return None
    next_url = urlunsplit(urlsplit(urljoin(instance_url + "/", next_records_url.lstrip("/"))))
    if not same_origin(instance_url, next_url):
        next_origin = urlsplit(next_url)
        origin = urlsplit(instance_url)
        raise SourceFetchError(
            f"nextRecordsUrl resolved to a different origin "
            f"({next_origin.scheme}://{next_origin.hostname}) than the Salesforce instance "
            f"({origin.scheme}://{origin.hostname}) — refusing to forward credentials off-host"
        )
    try:
        assert_http_url(next_url)
    except ConnectorSafetyError as exc:
        raise SourceFetchError(str(exc)) from exc
    return next_url


async def fetch_for_dataset(
    pool: asyncpg.Pool, tenant_id: str, name: str
) -> tuple[list[dict], Optional[Callable[[], Awaitable[None]]]]:
    row = await pool.fetchrow(
        "SELECT connection_name, soql, api_version, cursor_property, last_cursor_value "
        "FROM salesforce_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    )
    if row is None:
        raise SourceFetchError(f"no active Salesforce source registered as {name!r}")

    connection = await pool.fetchrow(
        "SELECT login_url, client_id, client_secret, secret_ref, oauth2_cached_token, "
        "oauth2_token_expires_at, instance_url FROM salesforce_connection "
        "WHERE tenant_id = $1 AND name = $2",
        tenant_id, row["connection_name"],
    )
    if connection is None:
        raise SourceFetchError(
            f"source {name!r} references connection {row['connection_name']!r}, which no longer exists"
        )

    token, instance_url = await _bearer_token(pool, tenant_id, row["connection_name"], connection)
    soql = row["soql"]
    if row["cursor_property"] and row["last_cursor_value"] is not None:
        soql = _apply_cursor(soql, row["cursor_property"], row["last_cursor_value"])

    query_url = (
        f"{instance_url}/services/data/{row['api_version']}/query?{urlencode({'q': soql})}"
    )
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    records: list[dict] = []
    pages_fetched = 0
    next_url: Optional[str] = query_url

    async with httpx.AsyncClient(timeout=30.0) as client:
        while next_url:
            pages_fetched += 1
            if pages_fetched > _MAX_PAGES:
                raise SourceFetchError(
                    f"stopped after {_MAX_PAGES} pages without reaching the end — "
                    "narrow the SOQL or raise the page limit"
                )
            try:
                assert_http_url(next_url)
            except ConnectorSafetyError as exc:
                raise SourceFetchError(str(exc)) from exc
            response = await client.get(next_url, headers=headers)
            if response.status_code >= 400:
                raise SourceFetchError(
                    f"Salesforce query failed ({response.status_code}): {response.text[:300]}"
                )
            body = response.json()
            page_records = body.get("records")
            if not isinstance(page_records, list):
                raise SourceFetchError("Salesforce query response missing records array")
            records.extend(_strip_attributes(r) for r in page_records if isinstance(r, dict))
            if body.get("done", True):
                break
            next_url = _next_page_url(
                instance_url=instance_url, next_records_url=body.get("nextRecordsUrl")
            )

    commit: Optional[Callable[[], Awaitable[None]]] = None
    if row["cursor_property"]:
        candidates = [
            r[row["cursor_property"]]
            for r in records
            if r.get(row["cursor_property"]) is not None
        ]
        if candidates:
            new_cursor = str(max(candidates, key=_coerce_cursor))
            if new_cursor != row["last_cursor_value"]:

                async def _commit_cursor(cursor: str = new_cursor) -> None:
                    await pool.execute(
                        "UPDATE salesforce_source SET last_cursor_value = $1 "
                        "WHERE tenant_id = $2 AND name = $3",
                        cursor, tenant_id, name,
                    )

                commit = _commit_cursor

    return records, commit
