"""Inclusive incremental cursors.

A strict `cursor > last` filter drops rows that share the resume value but
arrive after the sync that recorded it (typical with second-precision
timestamps). Fetch with `>=` (or a one-step look-back on APIs we do not
control) and drop identities already stored at that boundary so each row
lands in the dataset once.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

# One hot timestamp must not store an unbounded identity list.
_MAX_BOUNDARY_KEYS = 4096
_ISO_START = re.compile(r"^\d{4}-\d{2}-\d{2}(?:$|[T ])")


def _time_key(text: str) -> Optional[datetime]:
    """UTC datetime for an ISO date or timestamp, else None.

    `Z` and `+0000` are the same instant, so a fractional suffix is not
    ordered as text against a cursor that has none.
    """
    raw = text.strip()
    if not _ISO_START.match(raw):
        return None
    iso = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    matched = re.fullmatch(r"(.*[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2})(\d{2})", iso)
    if matched:
        iso = f"{matched.group(1)}{matched.group(2)}:{matched.group(3)}"
    if len(iso) > 10 and iso[10] == " ":
        iso = iso[:10] + "T" + iso[11:]
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _cursor_key(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = str(value).strip()
    if text.isdigit() or (text.startswith("-") and len(text) > 1 and text[1:].isdigit()):
        return int(text)
    parsed = _time_key(text)
    return parsed if parsed is not None else text


def _store_cursor(raw: Any) -> str:
    if isinstance(raw, datetime):
        return raw.isoformat()
    return str(raw).strip()


def _cap_boundary(keys: set[str]) -> set[str]:
    if len(keys) <= _MAX_BOUNDARY_KEYS:
        return keys
    return set(sorted(keys)[:_MAX_BOUNDARY_KEYS])


def _cmp(left: Any, right: Any) -> int:
    lk, rk = _cursor_key(left), _cursor_key(right)
    if type(lk) is not type(rk):
        lk, rk = str(lk), str(rk)
    return (lk > rk) - (lk < rk)


def row_identity(row: dict) -> str:
    payload = json.dumps(row, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def parse_boundary_keys(raw: Optional[str]) -> set[str]:
    if not raw:
        return set()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return set()
    if not isinstance(data, list):
        return set()
    return {str(item) for item in data}


def dump_boundary_keys(keys: set[str]) -> str:
    return json.dumps(sorted(keys), separators=(",", ":"))


def lookback_value(last: str) -> str:
    """One step behind `last` for remote filters we cannot switch to `>=`.

    Integers move by 1. ISO-8601 timestamps move by one second. Anything
    else is returned unchanged — the caller still de-duplicates the boundary.
    """
    key = _cursor_key(last)
    if isinstance(key, int) and not isinstance(key, bool):
        return str(key - 1)
    text = str(last).strip()
    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        return text
    earlier = parsed - timedelta(seconds=1)
    if text.endswith("Z"):
        if earlier.tzinfo is None:
            earlier = earlier.replace(tzinfo=timezone.utc)
        return earlier.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return earlier.isoformat()


@dataclass(frozen=True)
class CursorAdvance:
    rows: list[dict]
    cursor: Optional[str]
    boundary_keys: str
    changed: bool


def advance_cursor(
    records: list[dict],
    *,
    cursor_property: str,
    last_cursor: Optional[str],
    boundary_keys: Optional[str],
) -> CursorAdvance:
    """Keep rows at or after `last_cursor`, except identities already seen there."""
    seen = parse_boundary_keys(boundary_keys)
    last_key = _cursor_key(last_cursor) if last_cursor is not None else None
    kept: list[dict] = []
    boundary_ids: set[str] = set()
    newest: Any = None
    newest_raw: Any = None

    for record in records:
        raw = record.get(cursor_property)
        if raw is None:
            if last_key is None:
                kept.append(record)
            continue
        key = _cursor_key(raw)
        ident = row_identity(record)
        if last_key is not None:
            order = _cmp(key, last_key)
            if order < 0:
                continue
            if order == 0 and ident in seen:
                continue
        kept.append(record)
        if newest is None or _cmp(key, newest) > 0:
            newest = key
            newest_raw = raw
            boundary_ids = {ident}
        elif _cmp(key, newest) == 0:
            boundary_ids.add(ident)

    if newest is None:
        return CursorAdvance(
            rows=kept,
            cursor=last_cursor,
            boundary_keys=dump_boundary_keys(_cap_boundary(seen)),
            changed=False,
        )

    if last_key is not None and _cmp(newest, last_key) == 0:
        new_cursor = last_cursor
        merged = seen | boundary_ids
    else:
        new_cursor = _store_cursor(newest_raw)
        merged = boundary_ids
    merged = _cap_boundary(merged)

    encoded = dump_boundary_keys(merged)
    changed = new_cursor != last_cursor or encoded != dump_boundary_keys(seen)
    return CursorAdvance(rows=kept, cursor=new_cursor, boundary_keys=encoded, changed=changed)
