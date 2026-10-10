"""SQL query fetch and cursor binding for no-code SQL sources."""
from __future__ import annotations

import datetime
import re
from typing import Any, Awaitable, Callable, Optional

import asyncpg

from app import sql_drivers
from app.cursor_window import advance_cursor
from app.source_registry_base import (
    SourceConfigError,
    SourceFetchError,
    make_column_cursor_commit,
    make_property_cursor_commit,
    resolve_source_secret,
)
from app.sql_source_validation import _require_select_only
from holon_common.connector_safety import ConnectorSafetyError, connector_secret, pin_connector_host
from holon_common.sql_ident import quote_identifier

_ISO_CURSOR_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}:\d{2}(?:\.\d+)?)(Z|[+-]\d{2}:?\d{2})?)?$"
)

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

def _cursor_to_str(value: Any) -> str:
    """Persist date/datetime cursors as ISO-8601 so they round-trip on bind."""
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    return str(value)

def _bind_cursor_value(value: str) -> Any:
    """Coerce a stored cursor string so drivers can bind typed columns.

    Avoids a dialect-specific catalog lookup (pg_attribute) while still
    comparing integers/floats/timestamps natively instead of lexicographically.
    A bare ISO string bound against a `timestamptz` column fails on asyncpg
    (it does not implicitly cast text -> timestamptz), so date/datetime
    cursors are parsed into `datetime.datetime` — timezone-aware when the
    value carries a 'Z' or UTC offset, naive otherwise.
    """
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        return int(value)
    try:
        return float(value)
    except ValueError:
        pass
    parsed = _parse_iso_cursor(value)
    return parsed if parsed is not None else value

def _row_get(row: dict, key: str, *, dialect: str) -> Any:
    """Read a column from a driver row dict.

    Snowflake DictCursor returns unquoted column names in UPPER case, so
    a cursor_property entered as `updated_at` must still match `UPDATED_AT`.
    """
    if key in row:
        return row[key]
    if dialect == "snowflake":
        upper = key.upper()
        for name, value in row.items():
            if isinstance(name, str) and name.upper() == upper:
                return value
    return None

async def fetch_for_dataset(
    pool: asyncpg.Pool, tenant_id: str, name: str
) -> tuple[list[dict], Optional[Callable[[], Awaitable[None]]]]:
    row = await pool.fetchrow(
        "SELECT connection_name, table_name, query, cursor_property, last_cursor_value, "
        "cursor_boundary_keys "
        "FROM sql_source WHERE tenant_id = $1 AND name = $2 AND status = 'active'",
        tenant_id, name,
    )
    if row is None:
        raise SourceFetchError(f"no active SQL source registered as {name!r}")

    connection = await pool.fetchrow(
        "SELECT dialect, host, port, database, warehouse, username, password, secret_ref "
        "FROM sql_connection WHERE tenant_id = $1 AND name = $2",
        tenant_id, row["connection_name"],
    )
    if connection is None:
        raise SourceFetchError(f"source {name!r} references connection {row['connection_name']!r}, which no longer exists")
    try:
        pinned_host = pin_connector_host(connection["host"])
    except ConnectorSafetyError as exc:
        raise SourceFetchError(str(exc)) from exc
    password = connector_secret(
        secret_ref=connection["secret_ref"],
        plaintext=connection["password"],
        resolved=resolve_source_secret(connection["secret_ref"], tenant_id=tenant_id),
    )

    try:
        dialect = sql_drivers.normalize_dialect(connection["dialect"])
    except ValueError as exc:
        raise SourceFetchError(str(exc)) from exc
    wire = sql_drivers.wire_dialect(dialect)

    if row["table_name"]:
        sql = f"SELECT * FROM {quote_identifier(row['table_name'], dialect=wire)}"
        args: list[Any] = []
        if row["cursor_property"] and row["last_cursor_value"] is not None:
            # Uniform bind across dialects (no pg_attribute type cast).
            col = quote_identifier(row["cursor_property"], dialect=wire)
            sql += f" WHERE {col} >= {sql_drivers.cursor_placeholder(dialect)}"
            args.append(_bind_cursor_value(row["last_cursor_value"]))
    else:
        sql = row["query"]
        args = []
        try:
            _require_select_only(sql, dialect)
        except SourceConfigError as exc:
            raise SourceFetchError(str(exc)) from exc

    try:
        rows = await sql_drivers.fetch_dicts(
            dialect=dialect,
            host=pinned_host,
            port=connection["port"],
            database=connection["database"],
            username=connection["username"],
            password=password,
            sql=sql,
            args=args,
            warehouse=connection["warehouse"],
        )
    except Exception as exc:
        # Drivers raise a mix of OSError, asyncpg/aiomysql/aioodbc errors.
        raise SourceFetchError(f"could not fetch source {name!r}: {exc}") from exc

    commit: Optional[Callable[[], Awaitable[None]]] = None
    if row["table_name"] and row["cursor_property"]:
        advanced = advance_cursor(
            rows,
            cursor_property=row["cursor_property"],
            last_cursor=row["last_cursor_value"],
            boundary_keys=row["cursor_boundary_keys"],
        )
        rows = advanced.rows
        if advanced.changed and advanced.cursor is not None:

            commit = make_property_cursor_commit(
                pool,
                table="sql_source",
                tenant_id=tenant_id,
                name=name,
                cursor=advanced.cursor,
                boundary_keys=advanced.boundary_keys,
            )
    elif row["cursor_property"]:
        cursor_key = row["cursor_property"]
        candidates = [
            value
            for r in rows
            if (value := _row_get(r, cursor_key, dialect=wire)) is not None
        ]
        if candidates:
            new_cursor = _cursor_to_str(max(candidates))
            if new_cursor != row["last_cursor_value"]:

                commit = make_column_cursor_commit(
                    pool,
                    table="sql_source",
                    tenant_id=tenant_id,
                    name=name,
                    column="last_cursor_value",
                    value=new_cursor,
                )

    return rows, commit

