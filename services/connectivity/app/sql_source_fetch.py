"""SQL query fetch and cursor binding for no-code SQL sources."""
from __future__ import annotations

import datetime
import re
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

import asyncpg

from app import sql_drivers
from app.cursor_window import CursorTracker
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
    pool: asyncpg.Pool, tenant_id: str, name: str, *, batch_size: int = 10_000
) -> tuple[AsyncIterator[list[dict]], Callable[[], Awaitable[None]]]:
    """Batches of the source's rows, and the cursor commit to run once they are all written."""
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

    incremental_table = bool(row["table_name"] and row["cursor_property"])
    tracker = (
        CursorTracker(
            cursor_property=row["cursor_property"],
            last_cursor=row["last_cursor_value"],
            boundary_keys=row["cursor_boundary_keys"],
        )
        if incremental_table
        else None
    )
    query_cursor_key = row["cursor_property"] if not incremental_table else None
    newest: Any = None

    async def batches() -> AsyncIterator[list[dict]]:
        nonlocal newest
        stream = sql_drivers.stream_dicts(
            dialect=dialect,
            host=pinned_host,
            port=connection["port"],
            database=connection["database"],
            username=connection["username"],
            password=password,
            sql=sql,
            args=args,
            batch_size=batch_size,
            warehouse=connection["warehouse"],
        )
        while True:
            try:
                rows = await anext(stream)
            except StopAsyncIteration:
                return
            except Exception as exc:
                # Drivers raise a mix of OSError, asyncpg/aiomysql/aioodbc errors.
                raise SourceFetchError(f"could not fetch source {name!r}: {exc}") from exc
            if tracker is not None:
                rows = tracker.keep(rows)
            elif query_cursor_key:
                candidates = [
                    value
                    for r in rows
                    if (value := _row_get(r, query_cursor_key, dialect=wire)) is not None
                ]
                if candidates:
                    batch_max = max(candidates)
                    newest = batch_max if newest is None else max(newest, batch_max)
            if rows:
                yield rows

    async def commit() -> None:
        if tracker is not None:
            advanced = tracker.result()
            if advanced.changed and advanced.cursor is not None:
                await make_property_cursor_commit(
                    pool,
                    table="sql_source",
                    tenant_id=tenant_id,
                    name=name,
                    cursor=advanced.cursor,
                    boundary_keys=advanced.boundary_keys,
                )()
        elif newest is not None:
            new_cursor = _cursor_to_str(newest)
            if new_cursor != row["last_cursor_value"]:
                await make_column_cursor_commit(
                    pool,
                    table="sql_source",
                    tenant_id=tenant_id,
                    name=name,
                    column="last_cursor_value",
                    value=new_cursor,
                )()

    return batches(), commit
