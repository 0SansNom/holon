"""Salesforce SOQL fetch / pagination for no-code sources."""
from __future__ import annotations

import datetime
import re
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

import asyncpg
import httpx

from app.cursor_window import advance_cursor
from app.pinned_http import pinned_transport
from app.source_registry_base import (
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

_ISO_CURSOR_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}:\d{2}(?:\.\d+)?)(Z|[+-]\d{2}:?\d{2})?)?$"
)

_MAX_PAGES = 100

_OAUTH2_REFRESH_MARGIN_SECONDS = 60

_OAUTH2_DEFAULT_TTL_SECONDS = 300

def _parse_iso_cursor(value: str) -> Optional[datetime.datetime]:
    """Parse an ISO-8601 date/datetime cursor as stored or as sources emit it.

    Accepts 'T' or ' ' between date and time (``str(datetime)`` uses a
    space), 'Z', and ``±HH:MM`` or ``±HHMM`` offsets (Salesforce JSON uses
    ``+0000``). Date-only values parse to midnight. None when not a date.
    """
    match = _ISO_CURSOR_RE.match(value)
    if not match:
        return None
    day, clock, offset = match.groups()
    iso = day
    if clock:
        if "." in clock:
            whole, frac = clock.split(".", 1)
            clock = f"{whole}.{frac[:6].ljust(6, '0')}"
        iso = f"{day}T{clock}"
        if offset:
            if offset == "Z":
                offset = "+00:00"
            elif ":" not in offset:
                offset = f"{offset[:3]}:{offset[3:]}"
            iso += offset
    try:
        return datetime.datetime.fromisoformat(iso)
    except ValueError:
        return None


def _soql_date_literal(value: str) -> Optional[str]:
    """Render a date/datetime cursor as an unquoted SOQL literal, else None.

    Datetimes go out as UTC ``YYYY-MM-DDThh:mm:ssZ``: Salesforce emits
    ``2024-01-15T10:30:00.000+0000`` in JSON, which is not a SOQL literal.
    Dropping the milliseconds moves the cursor back by < 1s, so the next
    sync may re-read a few rows but never skips one.
    """
    parsed = _parse_iso_cursor(value)
    if parsed is None:
        return None
    if _ISO_CURSOR_RE.match(value).group(2) is None:
        return parsed.date().isoformat()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _apply_cursor(soql: str, cursor_property: str, last_cursor_value: str) -> str:
    """Append an incremental filter without rewriting the user's SELECT list."""
    date_literal = _soql_date_literal(last_cursor_value)
    if date_literal is not None:
        # Inclusive resume (`>=`) plus boundary de-dupe keeps rows that share
        # the cursor value. SOQL Date/DateTime literals must be unquoted.
        clause = f"{cursor_property} >= {date_literal}"
    else:
        literal = last_cursor_value.replace("\\", "\\\\").replace("'", "\\'")
        clause = f"{cursor_property} >= '{literal}'"
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

    client_secret = connector_secret(
        secret_ref=connection["secret_ref"],
        plaintext=connection["client_secret"],
        resolved=resolve_source_secret(connection["secret_ref"], tenant_id=tenant_id),
    )
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
    async with httpx.AsyncClient(transport=pinned_transport(), timeout=15.0) as client:
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
        "SELECT connection_name, soql, api_version, cursor_property, last_cursor_value, "
        "cursor_boundary_keys "
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

    async with httpx.AsyncClient(transport=pinned_transport(), timeout=30.0) as client:
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
                table="salesforce_source",
                tenant_id=tenant_id,
                name=name,
                cursor=advanced.cursor,
                boundary_keys=advanced.boundary_keys,
            )

    return records, commit
