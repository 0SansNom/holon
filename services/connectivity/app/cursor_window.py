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
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional


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
        return value.isoformat()
    text = str(value).strip()
    if text.isdigit() or (text.startswith("-") and len(text) > 1 and text[1:].isdigit()):
        return int(text)
    return text


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


class CursorTracker:
    """`advance_cursor` fed batch by batch, so a sync never holds every row at once."""

    def __init__(self, *, cursor_property: str, last_cursor: Optional[str], boundary_keys: Optional[str]) -> None:
        self._cursor_property = cursor_property
        self._last_cursor = last_cursor
        self._seen = parse_boundary_keys(boundary_keys)
        self._last_key = _cursor_key(last_cursor) if last_cursor is not None else None
        self._newest: Any = None
        self._boundary_ids: set[str] = set()

    def keep(self, records: list[dict]) -> list[dict]:
        """Rows at or after the stored cursor, except identities already stored at it."""
        kept: list[dict] = []
        for record in records:
            raw = record.get(self._cursor_property)
            if raw is None:
                if self._last_key is None:
                    kept.append(record)
                continue
            key = _cursor_key(raw)
            ident = row_identity(record)
            if self._last_key is not None:
                order = _cmp(key, self._last_key)
                if order < 0:
                    continue
                if order == 0 and ident in self._seen:
                    continue
            kept.append(record)
            if self._newest is None or _cmp(key, self._newest) > 0:
                self._newest = key
                self._boundary_ids = {ident}
            elif _cmp(key, self._newest) == 0:
                self._boundary_ids.add(ident)
        return kept

    def result(self) -> CursorAdvance:
        """The cursor to store once every kept row is written (`rows` is left empty)."""
        if self._newest is None:
            return CursorAdvance(
                rows=[], cursor=self._last_cursor, boundary_keys=dump_boundary_keys(self._seen), changed=False
            )
        if self._last_key is not None and _cmp(self._newest, self._last_key) == 0:
            new_cursor = self._last_cursor
            merged = self._seen | self._boundary_ids
        else:
            new_cursor = str(self._newest)
            merged = self._boundary_ids
        encoded = dump_boundary_keys(merged)
        changed = new_cursor != self._last_cursor or encoded != dump_boundary_keys(self._seen)
        return CursorAdvance(rows=[], cursor=new_cursor, boundary_keys=encoded, changed=changed)


def advance_cursor(
    records: list[dict],
    *,
    cursor_property: str,
    last_cursor: Optional[str],
    boundary_keys: Optional[str],
) -> CursorAdvance:
    """Keep rows at or after `last_cursor`, except identities already seen there."""
    tracker = CursorTracker(cursor_property=cursor_property, last_cursor=last_cursor, boundary_keys=boundary_keys)
    kept = tracker.keep(records)
    return replace(tracker.result(), rows=kept)
