"""Generic REST fetch / pagination / OAuth token cache for no-code sources."""
from __future__ import annotations

import datetime
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import asyncpg
import httpx

from app.cursor_window import advance_cursor, lookback_value
from app.pinned_http import pinned_transport
from app.source_registry_base import (
    SourceConfigError,
    SourceFetchError,
    make_property_cursor_commit,
    resolve_source_secret,
)
from holon_common.connector_safety import (
    ConnectorSafetyError,
    assert_http_url,
    connector_secret,
    same_origin,
)

# Maximum page count safety limit to prevent pagination infinite loops.
_MAX_PAGES = 100

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

    client_secret = connector_secret(
        secret_ref=connection["secret_ref"],
        plaintext=connection["oauth2_client_secret"],
        resolved=resolve_source_secret(connection["secret_ref"], tenant_id=tenant_id),
    )
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
    async with httpx.AsyncClient(transport=pinned_transport(), timeout=15.0) as client:
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


async def fetch_for_dataset(
    pool: asyncpg.Pool, tenant_id: str, name: str
) -> tuple[list[dict], Optional[Callable[[], Awaitable[None]]]]:
    row = await pool.fetchrow(
        "SELECT base_url, auth_header_name, auth_header_value, secret_ref, record_path, next_page_path, connection_name, "
        "cursor_property, incremental_param, last_cursor_value, cursor_boundary_keys "
        "FROM generic_rest_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    )
    if row is None:
        raise SourceFetchError(f"no active generic REST source registered as {name!r}")

    headers: dict[str, str] = {}
    if row["connection_name"]:
        connection = await pool.fetchrow(
            "SELECT auth_type, auth_header_name, auth_header_value, secret_ref, oauth2_token_url, oauth2_client_id, "
            "oauth2_client_secret, oauth2_scope, oauth2_cached_token, oauth2_token_expires_at, allowed_origin "
            "FROM generic_rest_connection WHERE tenant_id = $1 AND name = $2",
            tenant_id, row["connection_name"],
        )
        if connection is None:
            raise SourceFetchError(
                f"source {name!r} references connection {row['connection_name']!r}, which no longer exists"
            )
        # Re-checked at fetch: the connection's origin may have been edited
        # after this source was saved.
        try:
            from app.generic_source_registry import _assert_connection_origin
            _assert_connection_origin(row["connection_name"], connection["allowed_origin"], row["base_url"])
        except SourceConfigError as exc:
            raise SourceFetchError(str(exc)) from exc
        if connection["auth_type"] == "oauth2_client_credentials":
            token = await _oauth2_bearer_token(pool, tenant_id, row["connection_name"], connection)
            headers["Authorization"] = f"Bearer {token}"
        else:
            value = connector_secret(
                secret_ref=connection["secret_ref"],
                plaintext=connection["auth_header_value"],
                resolved=resolve_source_secret(connection["secret_ref"], tenant_id=tenant_id),
            )
            headers[connection["auth_header_name"]] = value
    elif row["auth_header_name"]:
        value = connector_secret(
            secret_ref=row["secret_ref"],
            plaintext=row["auth_header_value"],
            resolved=resolve_source_secret(row["secret_ref"], tenant_id=tenant_id),
        )
        if value:
            headers[row["auth_header_name"]] = value

    records: list[dict] = []
    url: Optional[str] = row["base_url"]
    try:
        assert_http_url(url)
    except ConnectorSafetyError as exc:
        raise SourceFetchError(str(exc)) from exc
    # Append incremental parameter only to the first page request.
    if row["incremental_param"] and row["last_cursor_value"] is not None:
        url = _add_query_param(url, row["incremental_param"], lookback_value(row["last_cursor_value"]))
    pages_fetched = 0

    async with httpx.AsyncClient(transport=pinned_transport(), timeout=15.0) as client:
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

    commit: Optional[Callable[[], Awaitable[None]]] = None
    if row["cursor_property"]:
        advanced = advance_cursor(
            records,
            cursor_property=row["cursor_property"],
            last_cursor=row["last_cursor_value"],
            boundary_keys=row["cursor_boundary_keys"],
        )
        records = advanced.rows
        if advanced.changed and advanced.cursor is not None:

            commit = make_property_cursor_commit(
                pool,
                table="generic_rest_source",
                tenant_id=tenant_id,
                name=name,
                cursor=advanced.cursor,
                boundary_keys=advanced.boundary_keys,
            )

    return records, commit
