"""Generic REST source registry — the no-code connector: a non-technical
admin registers a new data source by filling in a URL and an optional
auth header, entirely as data (one row in Postgres), with zero Python to
write or deploy. `main.py`'s `run_sync` dispatch checks this registry
after `plugin_registry` (developer-authored plugins registered via API) — the
same zero-arg-async-reader shape both share, so `_finalize_sync`
downstream needs no changes at all.

Deliberately narrower than a real ConnectorPlugin: one HTTP GET per page,
one optional bearer/API-key header, one optional dot-path to the record
array inside the JSON body, one optional dot-path to a next-page URL.
That covers a large, genuinely common family of REST APIs — anything
that hands back its own absolute next-page link in the body (Django REST
Framework's default `{"results": [...], "next": "https://...", ...}`
shape, and plenty of others styled the same way) — without asking a
non-technical admin to configure a page-number/offset scheme by hand.
APIs that only give you a page number or a `has_more` flag and expect
*you* to construct the next URL, POST bodies, or multi-step auth are a
real Python plugin's job, not this one's — the point isn't to replace
plugins, it's to remove the "write and deploy Python" requirement for
the common case.

Secret handling: prefer `secret_ref` (`env:…` / `vault:…` / `k8s:…` /
`aws:…`) resolved at sync-time via `holon_common.secrets`. Inline
`auth_header_value` remains supported for local demo only — do not store
production API keys as plaintext.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg
import httpx

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

-- Reusable auth, separate from any one source (the n8n/Activepieces
-- idea): connect three endpoints on the same API and configure the
-- credential once, not three times. A source either references a
-- connection by name *or* declares its own inline auth_header_name/
-- auth_header_value — never both (enforced in `register_source`), so
-- there's exactly one place a given source's credential actually lives.
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

-- Scheduling (the Kestra idea: decouple "when" from "how") — an
-- optional interval, not a cron expression. A non-technical admin
-- configures "every N minutes" without learning cron syntax, and it's
-- one less dependency (no croniter) for a background loop to need.
-- `main.py`'s scheduler task compares this against `sync_run.finished_at`
-- (already recorded by every sync regardless of trigger) rather than a
-- second last-run column here — one source of truth for "when did this
-- last actually sync", not two that could drift apart.
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS schedule_interval_minutes INTEGER;

-- Incremental sync (the Meltano/Singer idea: a minimal resume state) —
-- `cursor_property` names the field to watch in each record (e.g.
-- "updated_at"); `incremental_param` names the query parameter this
-- connector adds to ask the API for only what changed since
-- `last_cursor_value`. The admin sets the first two; `last_cursor_value`
-- is system-managed (computed after every fetch), never admin-entered —
-- there being nothing meaningful for a human to type into "the last
-- value we happened to see" ahead of the first sync. Whether this
-- actually reduces load on the *source* depends on that API respecting
-- an added query parameter it may never have heard of; if it doesn't,
-- the connector still asks for (and safely re-processes) everything —
-- graceful degradation, never silent data loss.
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS cursor_property TEXT;
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS incremental_param TEXT;
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS last_cursor_value TEXT;

-- Workspace the source lands datasets into (ADR 026 multi-tenant). NULL
-- means "use caller/env default at sync time" for rows created before this
-- column existed; new registrations always store an explicit workspace_id.
ALTER TABLE generic_rest_source ADD COLUMN IF NOT EXISTS workspace_id TEXT;
"""

# Columns safe to hand back to a caller — never auth_header_value itself,
# only whether one is set, so an edit form can say "leave blank to keep
# the existing value" instead of implying there's nothing there yet.
_PUBLIC_COLUMNS = (
    "tenant_id, name, workspace_id, base_url, auth_header_name, (auth_header_value IS NOT NULL) AS has_auth_header_value, "
    "record_path, next_page_path, connection_name, schedule_interval_minutes, "
    "cursor_property, incremental_param, last_cursor_value, status, created_by_urn, created_at"
)

_CONNECTION_PUBLIC_COLUMNS = (
    "tenant_id, name, auth_header_name, (auth_header_value IS NOT NULL) AS has_auth_header_value, "
    "created_by_urn, created_at"
)

# A page pointing back to an already-visited URL (misconfiguration, or an
# API that never actually terminates its `next` chain) would otherwise
# hang a sync forever — this is what actually bounds that, not merely a
# generous-sounding limit. Any real paginated source in this build's
# demo scale finishes in 1-2 pages; 100 is headroom, not a target.
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


async def register_connection(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    auth_header_name: str,
    auth_header_value: Optional[str],
    created_by_urn: str,
) -> dict:
    """A real upsert (same `name` again updates it) — same intent
    `register_source` already covers for its own secret, but resolved
    in Python here rather than SQL `COALESCE`: `generic_rest_source`'s
    `auth_header_value` column is nullable (a source can have no auth at
    all), so `COALESCE(EXCLUDED.x, table.x)` inside `ON CONFLICT DO
    UPDATE` works there. This table's `auth_header_value` is `NOT NULL`
    (a connection *is* a stored secret — one without a value makes no
    sense), and Postgres validates `NOT NULL` against the raw proposed
    row *before* conflict resolution ever runs, so passing a literal
    `NULL` for it fails outright even when `ON CONFLICT DO UPDATE` would
    otherwise have coalesced it away — confirmed directly against
    Postgres, not assumed. Resolving "omitted means keep the existing
    secret" here, before the value ever reaches SQL, sidesteps that
    entirely.
    """
    if auth_header_value is None:
        existing = await pool.fetchval(
            "SELECT auth_header_value FROM generic_rest_connection WHERE tenant_id = $1 AND name = $2",
            tenant_id, name,
        )
        auth_header_value = existing

    await pool.execute(
        """
        INSERT INTO generic_rest_connection (tenant_id, name, auth_header_name, auth_header_value, created_by_urn)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            auth_header_name = EXCLUDED.auth_header_name,
            auth_header_value = EXCLUDED.auth_header_value
        """,
        tenant_id, name, auth_header_name, auth_header_value, created_by_urn,
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
    reserved_dataset_names: frozenset[str] = frozenset({"inventory_levels"}),
) -> dict:
    """Same dataset-ownership guard `plugin_registry.register_plugin`
    already enforces (a name can't shadow a reserved stream dataset or an
    active plugin visible to this tenant), checked here too since this
    registry is an equally real claimant on the same `dataset` namespace
    `run_sync` dispatches over.

    `connection_name` and inline `auth_header_name`/`auth_header_value`
    are mutually exclusive — a source's credential lives in exactly one
    place, never both at once (which one would `fetch_for_dataset` even
    trust if they disagreed?).
    """
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
            -- Never echoed back to a client (see `_PUBLIC_COLUMNS`), so an
            -- edit that doesn't resend it must not blank out the existing
            -- secret — only overwrite when a new value is actually given.
            auth_header_value = COALESCE(EXCLUDED.auth_header_value, generic_rest_source.auth_header_value),
            record_path = EXCLUDED.record_path,
            next_page_path = EXCLUDED.next_page_path,
            connection_name = EXCLUDED.connection_name,
            schedule_interval_minutes = EXCLUDED.schedule_interval_minutes,
            cursor_property = EXCLUDED.cursor_property,
            incremental_param = EXCLUDED.incremental_param,
            -- last_cursor_value deliberately absent here: it's resume
            -- state computed by `fetch_for_dataset`, not a form field, so
            -- re-submitting the edit form must never reset it to NULL.
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
    """Disable/enable without deleting: `is_registered`/`fetch_for_dataset`
    both already gate on `status = 'active'`, the same status-column
    contract `plugin_registry.set_plugin_status` uses — a disabled source
    takes effect on the very next sync attempt, no restart needed, and
    its configuration (URL, auth, record_path) is preserved for
    re-enabling later rather than lost.
    """
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


def _extract_next_url(body: Any, next_page_path: str) -> Optional[str]:
    """`null`/missing at the path means "no more pages" — the DRF
    convention (`"next": null` on the last page) this feature targets —
    so that's a normal stop, not an error. Anything present but not a
    string (e.g. the admin pointed `next_page_path` at the wrong field)
    is a real misconfiguration worth failing loudly on, the same
    treatment `_extract_records` already gives a bad `record_path`.
    """
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
    return data


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
            "SELECT auth_header_name, auth_header_value, secret_ref FROM generic_rest_connection WHERE tenant_id = $1 AND name = $2",
            tenant_id, row["connection_name"],
        )
        if connection is None:
            raise SourceFetchError(
                f"source {name!r} references connection {row['connection_name']!r}, which no longer exists"
            )
        value = resolve_optional(connection["secret_ref"]) or connection["auth_header_value"]
        headers[connection["auth_header_name"]] = value
    elif row["auth_header_name"]:
        value = resolve_optional(row["secret_ref"]) or row["auth_header_value"]
        if value:
            headers[row["auth_header_name"]] = value

    records: list[dict] = []
    url: Optional[str] = row["base_url"]
    # Only the *first* page's URL carries the incremental filter — a
    # `next_page_path` link is the API's own follow-up URL, which either
    # already encodes its own continuation state or doesn't care about
    # this parameter at all; re-appending it to every page risks a
    # duplicate/conflicting query parameter for no benefit.
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
            # next-page URL is usually on the same host/API and needs the
            # same credential, and re-sending an unnecessary header on a
            # same-origin request is harmless.
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            body = response.json()
            records.extend(_extract_records(body, row["record_path"]))
            url = _extract_next_url(body, row["next_page_path"]) if row["next_page_path"] else None

    if row["cursor_property"]:
        new_cursor = _compute_new_cursor(records, row["cursor_property"], row["last_cursor_value"])
        if new_cursor != row["last_cursor_value"]:
            await pool.execute(
                "UPDATE generic_rest_source SET last_cursor_value = $1 WHERE tenant_id = $2 AND name = $3",
                new_cursor, tenant_id, name,
            )

    return records
