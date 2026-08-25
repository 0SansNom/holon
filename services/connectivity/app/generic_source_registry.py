"""Generic REST source registry — no-code REST connector registry.

Allows registering REST data sources, authentication headers, record extraction paths, and pagination config.
"""

from __future__ import annotations

import datetime
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import asyncpg
import httpx

from holon_common.connector_safety import ConnectorSafetyError, assert_http_url, same_origin
from holon_common.secrets import resolve_optional

DDL = """
CREATE TABLE IF NOT EXISTS generic_rest_source (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    auth_header_name TEXT,
    auth_header_value TEXT,
    record_path TEXT,
    next_page_path TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

-- additive migration for databases seeded before this column existed
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS next_page_path TEXT;
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS secret_ref TEXT;

-- Reusable REST connection credentials table.
CREATE TABLE IF NOT EXISTS generic_rest_connection (
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    auth_header_name TEXT NOT NULL,
    auth_header_value TEXT NOT NULL DEFAULT '',
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

ALTER TABLE generic_rest_connection ADD COLUMN IF NOT EXISTS secret_ref TEXT;
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS connection_name TEXT;

-- OAuth2 client credentials support.
ALTER TABLE generic_rest_connection ADD COLUMN IF NOT EXISTS auth_type TEXT NOT NULL DEFAULT 'header';
ALTER TABLE generic_rest_connection ADD COLUMN IF NOT EXISTS oauth2_token_url TEXT;
ALTER TABLE generic_rest_connection ADD COLUMN IF NOT EXISTS oauth2_client_id TEXT;
ALTER TABLE generic_rest_connection ADD COLUMN IF NOT EXISTS oauth2_client_secret TEXT;
ALTER TABLE generic_rest_connection ADD COLUMN IF NOT EXISTS oauth2_scope TEXT;
-- Persisted cache for OAuth2 tokens.
ALTER TABLE generic_rest_connection ADD COLUMN IF NOT EXISTS oauth2_cached_token TEXT;
ALTER TABLE generic_rest_connection ADD COLUMN IF NOT EXISTS oauth2_token_expires_at TIMESTAMPTZ;

-- Scheduling interval in minutes (compared against sync_run.finished_at).
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS schedule_interval_minutes INTEGER;

-- Incremental sync configuration and system-managed cursor tracking.
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS cursor_property TEXT;
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS incremental_param TEXT;
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS last_cursor_value TEXT;

-- Target workspace for multi-tenant isolation.
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS workspace_id TEXT;
"""

# Columns safe to return to caller (excludes raw credential values).
_PUBLIC_COLUMNS = (
    "tenant_id, name, workspace_id, base_url, auth_header_name, (auth_header_value IS NOT NULL) AS has_auth_header_value, "
    "record_path, next_page_path, connection_name, schedule_interval_minutes, "
    "cursor_property, incremental_param, last_cursor_value, status, created_by_urn, created_at"
)

_CONNECTION_PUBLIC_COLUMNS = (
    "tenant_id, name, auth_type, auth_header_name, (auth_header_value IS NOT NULL) AS has_auth_header_value, "
    "oauth2_token_url, oauth2_client_id, (oauth2_client_secret IS NOT NULL) AS has_oauth2_client_secret, oauth2_scope, "
    "created_by_urn, created_at"
)

# Maximum page count safety limit to prevent pagination infinite loops.
_MAX_PAGES = 100


class SourceConflictError(ValueError):
    pass


class SourceConfigError(ValueError):
    pass


class SourceFetchError(ValueError):
    pass


class ConnectionConflictError(ValueError):
    pass


class ConnectionInUseError(ValueError):
    pass


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


_VALID_CONNECTION_AUTH_TYPES = frozenset({"header", "oauth2_client_credentials"})


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
        "SELECT auth_header_value, oauth2_client_secret FROM generic_rest_connection WHERE tenant_id = $1 AND name = $2",
        tenant_id, name,
    )
    if auth_header_value is None and existing is not None:
        auth_header_value = existing["auth_header_value"]
    if oauth2_client_secret is None and existing is not None:
        oauth2_client_secret = existing["oauth2_client_secret"]
    if auth_type == "oauth2_client_credentials" and not oauth2_client_secret:
        raise SourceConfigError("auth_type='oauth2_client_credentials' requires oauth2_client_secret")

    await pool.execute(
        """
        INSERT INTO generic_rest_connection (
            tenant_id, name, auth_type, auth_header_name, auth_header_value,
            oauth2_token_url, oauth2_client_id, oauth2_client_secret, oauth2_scope, created_by_urn
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            auth_type = EXCLUDED.auth_type,
            auth_header_name = EXCLUDED.auth_header_name,
            auth_header_value = EXCLUDED.auth_header_value,
            oauth2_token_url = EXCLUDED.oauth2_token_url,
            oauth2_client_id = EXCLUDED.oauth2_client_id,
            oauth2_client_secret = EXCLUDED.oauth2_client_secret,
            oauth2_scope = EXCLUDED.oauth2_scope,
            -- A re-registration changes credentials; the cached token
            -- from the old ones must not survive it.
            oauth2_cached_token = NULL,
            oauth2_token_expires_at = NULL
        """,
        tenant_id, name, auth_type, auth_header_name, auth_header_value,
        oauth2_token_url, oauth2_client_id, oauth2_client_secret, oauth2_scope, created_by_urn,
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
    in_use = await pool.fetch(
        "SELECT name FROM generic_rest_source WHERE tenant_id = $1 AND connection_name = $2", tenant_id, name
    )
    if in_use:
        source_names = [row["name"] for row in in_use]
        raise ConnectionInUseError(
            f"connection {name!r} is still used by source(s) {source_names} — repoint or delete them first"
        )
    await pool.execute("DELETE FROM generic_rest_connection WHERE tenant_id = $1 AND name = $2", tenant_id, name)


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
    if connection_name and (auth_header_name or auth_header_value):
        raise SourceConfigError(
            "a source can use a named connection or its own inline auth header, not both — "
            "clear one before setting the other"
        )
    if connection_name and await get_connection(pool, tenant_id, connection_name) is None:
        raise SourceConfigError(f"unknown connection: {connection_name!r}")
    if schedule_interval_minutes is not None and schedule_interval_minutes <= 0:
        raise SourceConfigError("schedule_interval_minutes must be a positive number of minutes")

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
        raise SourceConflictError(f"dataset {name!r} is already claimed by active plugin {conflicting_plugin!r}")

    conflicting_sql_source = await pool.fetchval(
        "SELECT name FROM sql_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    )
    if conflicting_sql_source is not None:
        raise SourceConflictError(f"dataset {name!r} is already claimed by active SQL source {conflicting_sql_source!r}")

    conflicting_object_source = await pool.fetchval(
        "SELECT name FROM object_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    )
    if conflicting_object_source is not None:
        raise SourceConflictError(f"dataset {name!r} is already claimed by active object source {conflicting_object_source!r}")

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
    """Every active source with a schedule for one tenant — see
    `list_all_scheduled_sources` for the cross-tenant scheduler path.
    """
    rows = await pool.fetch(
        "SELECT name, schedule_interval_minutes FROM generic_rest_source "
        "WHERE tenant_id = $1 AND status = 'active' AND schedule_interval_minutes IS NOT NULL",
        tenant_id,
    )
    return [dict(row) for row in rows]


async def list_all_scheduled_sources(pool: asyncpg.Pool) -> list[dict]:
    """Active scheduled sources across every tenant (scheduler loop)."""
    rows = await pool.fetch(
        "SELECT tenant_id, name, workspace_id, schedule_interval_minutes FROM generic_rest_source "
        "WHERE status = 'active' AND schedule_interval_minutes IS NOT NULL"
    )
    return [dict(row) for row in rows]


async def set_source_status(pool: asyncpg.Pool, tenant_id: str, name: str, status: str) -> Optional[dict]:
    """Set active/disabled status for a REST source."""
    await pool.execute(
        "UPDATE generic_rest_source SET status = $1 WHERE tenant_id = $2 AND name = $3", status, tenant_id, name
    )
    return await get_source(pool, tenant_id, name)


async def delete_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> None:
    await pool.execute("DELETE FROM generic_rest_source WHERE tenant_id = $1 AND name = $2", tenant_id, name)


async def get_source(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        f"SELECT {_PUBLIC_COLUMNS} FROM generic_rest_source WHERE tenant_id = $1 AND name = $2", tenant_id, name
    )
    return None if row is None else dict(row)


async def list_sources(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        f"SELECT {_PUBLIC_COLUMNS} FROM generic_rest_source WHERE tenant_id = $1 ORDER BY name", tenant_id
    )
    return [dict(row) for row in rows]


async def is_registered(pool: asyncpg.Pool, tenant_id: str, name: str) -> bool:
    return await pool.fetchval(
        "SELECT true FROM generic_rest_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    ) or False


def _extract_records(body: Any, record_path: Optional[str]) -> list[dict]:
    data = body
    if record_path:
        for key in record_path.split("."):
            if not isinstance(data, dict) or key not in data:
                raise SourceFetchError(f"record_path {record_path!r}: no {key!r} field in the response at that point")
            data = data[key]
    if not isinstance(data, list):
        kind = type(data).__name__
        raise SourceFetchError(
            f"expected a JSON array at record_path={record_path!r}, got {kind} instead — "
            "set record_path to the dotted key that holds the array (e.g. 'data.items')"
        )
    return data


def _extract_next_url(body: Any, next_page_path: str, *, origin_url: str) -> Optional[str]:
    """Extract next page URL from paginated response, enforcing same-origin safety."""
    data: Any = body
    for key in next_page_path.split("."):
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    if data is None:
        return None
    if not isinstance(data, str) or not data:
        raise SourceFetchError(
            f"next_page_path {next_page_path!r}: expected a URL string or null, got {data!r}"
        )
    next_url = urlunsplit(urlsplit(urljoin(origin_url, data)))
    if not same_origin(origin_url, next_url):
        next_origin = urlsplit(next_url)
        origin = urlsplit(origin_url)
        raise SourceFetchError(
            f"next_page_path {next_page_path!r} resolved to a different origin "
            f"({next_origin.scheme}://{next_origin.hostname}) than the configured source "
            f"({origin.scheme}://{origin.hostname}) — refusing to forward credentials off-host"
        )
    try:
        assert_http_url(next_url)
    except ConnectorSafetyError as exc:
        raise SourceFetchError(str(exc)) from exc
    return next_url


def _coerce_cursor(value: Any) -> Any:
    """Same try-int-else-string trick `resolver.fetch_generic` already
    uses for `id_value` — a self-serve source's cursor field type isn't
    known ahead of time either, and comparing "10" < "9" as strings would
    silently pick the wrong "newest" value for a numeric cursor.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def _compute_new_cursor(records: list[dict], cursor_property: str, previous: Optional[str]) -> Optional[str]:
    """The new resume point is simply the largest value seen for
    `cursor_property` across every record just fetched (plus the previous
    cursor, so a page with no new rows never regresses it). Comparable
    only when every candidate coerces the same way (all int, or all
    string); a source that mixes types for the same field is treated as
    string-comparable — the same "don't guess a convention" stance this
    connector takes everywhere else.
    """
    candidates = [_coerce_cursor(record[cursor_property]) for record in records if record.get(cursor_property) is not None]
    if previous is not None:
        candidates.append(_coerce_cursor(previous))
    if not candidates:
        return previous
    try:
        newest = max(candidates)
    except TypeError:
        newest = max(str(candidate) for candidate in candidates)
    return str(newest)


def _add_query_param(url: str, key: str, value: str) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append((key, value))
    return urlunsplit(parsed._replace(query=urlencode(query)))


# Refresh the token this many seconds before it actually expires, rather
# than cutting it as close as possible.
_OAUTH2_REFRESH_MARGIN_SECONDS = 60
# Fallback TTL when the IdP's token response omits expires_in.
_OAUTH2_DEFAULT_TTL_SECONDS = 300


async def _oauth2_bearer_token(pool: asyncpg.Pool, tenant_id: str, connection_name: str, connection: asyncpg.Record) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = connection["oauth2_token_expires_at"]
    if connection["oauth2_cached_token"] and expires_at is not None:
        if (expires_at - now).total_seconds() > _OAUTH2_REFRESH_MARGIN_SECONDS:
            return connection["oauth2_cached_token"]

    client_secret = resolve_optional(connection["secret_ref"]) or connection["oauth2_client_secret"]
    form = {
        "grant_type": "client_credentials",
        "client_id": connection["oauth2_client_id"],
        "client_secret": client_secret,
    }
    if connection["oauth2_scope"]:
        form["scope"] = connection["oauth2_scope"]

    try:
        assert_http_url(connection["oauth2_token_url"])
    except ConnectorSafetyError as exc:
        raise SourceFetchError(str(exc)) from exc
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(connection["oauth2_token_url"], data=form)
    if response.status_code >= 400:
        raise SourceFetchError(
            f"connection {connection_name!r}: OAuth2 token request failed "
            f"({response.status_code}): {response.text[:300]}"
        )
    body = response.json()
    token = body.get("access_token")
    if not token:
        raise SourceFetchError(f"connection {connection_name!r}: OAuth2 token response had no access_token")
    ttl_seconds = body.get("expires_in") or _OAUTH2_DEFAULT_TTL_SECONDS
    new_expires_at = now + datetime.timedelta(seconds=int(ttl_seconds))

    await pool.execute(
        "UPDATE generic_rest_connection SET oauth2_cached_token = $1, oauth2_token_expires_at = $2 "
        "WHERE tenant_id = $3 AND name = $4",
        token, new_expires_at, tenant_id, connection_name,
    )
    return token


async def fetch_for_dataset(pool: asyncpg.Pool, tenant_id: str, name: str) -> list[dict]:
    row = await pool.fetchrow(
        "SELECT base_url, auth_header_name, auth_header_value, secret_ref, record_path, next_page_path, connection_name, "
        "cursor_property, incremental_param, last_cursor_value "
        "FROM generic_rest_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    )
    if row is None:
        raise SourceFetchError(f"no active generic REST source registered as {name!r}")

    headers: dict[str, str] = {}
    if row["connection_name"]:
        connection = await pool.fetchrow(
            "SELECT auth_type, auth_header_name, auth_header_value, secret_ref, oauth2_token_url, oauth2_client_id, "
            "oauth2_client_secret, oauth2_scope, oauth2_cached_token, oauth2_token_expires_at "
            "FROM generic_rest_connection WHERE tenant_id = $1 AND name = $2",
            tenant_id, row["connection_name"],
        )
        if connection is None:
            raise SourceFetchError(
                f"source {name!r} references connection {row['connection_name']!r}, which no longer exists"
            )
        if connection["auth_type"] == "oauth2_client_credentials":
            token = await _oauth2_bearer_token(pool, tenant_id, row["connection_name"], connection)
            headers["Authorization"] = f"Bearer {token}"
        else:
            value = resolve_optional(connection["secret_ref"]) or connection["auth_header_value"]
            headers[connection["auth_header_name"]] = value
    elif row["auth_header_name"]:
        value = resolve_optional(row["secret_ref"]) or row["auth_header_value"]
        if value:
            headers[row["auth_header_name"]] = value

    records: list[dict] = []
    url: Optional[str] = row["base_url"]
    try:
        assert_http_url(url)
    except ConnectorSafetyError as exc:
        raise SourceFetchError(str(exc)) from exc
    origin_url = url
    # Append incremental parameter only to the first page request.
    if row["incremental_param"] and row["last_cursor_value"] is not None:
        url = _add_query_param(url, row["incremental_param"], row["last_cursor_value"])
    pages_fetched = 0

    async with httpx.AsyncClient(timeout=15.0) as client:
        while url is not None:
            pages_fetched += 1
            if pages_fetched > _MAX_PAGES:
                raise SourceFetchError(
                    f"stopped after {_MAX_PAGES} pages without reaching the end — "
                    "either this source has more pages than this connector supports, or next_page_path "
                    "never resolves to null; a real connector plugin may be a better fit for this API"
                )
            # Auth header applied to every page, not just the first — the
            # next-page URL needs the same credential. `_extract_next_url`
            # pins the origin check to the *configured* `base_url`, not the
            # previous page's URL, so a malicious page N can't widen the
            # trusted origin for page N+1.
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            body = response.json()
            records.extend(_extract_records(body, row["record_path"]))
            url = (
                _extract_next_url(body, row["next_page_path"], origin_url=row["base_url"])
                if row["next_page_path"]
                else None
            )

    if row["cursor_property"]:
        new_cursor = _compute_new_cursor(records, row["cursor_property"], row["last_cursor_value"])
        if new_cursor != row["last_cursor_value"]:
            await pool.execute(
                "UPDATE generic_rest_source SET last_cursor_value = $1 WHERE tenant_id = $2 AND name = $3",
                new_cursor, tenant_id, name,
            )

    return records
