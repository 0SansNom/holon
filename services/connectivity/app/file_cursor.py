"""Resume file syncs by last-modified time, then key.

Lexicographic keys skip a file that is renamed, rewritten, or uploaded
out of order after a later name was already synced. The cursor is the
maximum `(mtime_ns, key)` pair read so far.
"""

from __future__ import annotations

import json
from typing import Optional


def encode_file_cursor(mtime_ns: int, key: str) -> str:
    return json.dumps(
        {"v": 2, "mtime_ns": int(mtime_ns), "key": key},
        separators=(",", ":"),
    )


def decode_file_cursor(cursor: Optional[str]) -> Optional[tuple[int, str]]:
    if not cursor or not str(cursor).startswith("{"):
        return None
    try:
        data = json.loads(cursor)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("v") != 2:
        return None
    try:
        return int(data["mtime_ns"]), str(data["key"])
    except (KeyError, TypeError, ValueError):
        return None


def file_cursor_key(cursor: Optional[str]) -> Optional[str]:
    parsed = decode_file_cursor(cursor)
    if parsed is not None:
        return parsed[1]
    return cursor or None


def info_mtime_ns(info: object) -> int:
    ns = getattr(info, "mtime_ns", None)
    if isinstance(ns, int):
        return ns
    return mtime_ns_from_stamp(getattr(info, "mtime", None))


def mtime_ns_from_stamp(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return int(value * 1_000_000_000)
    timestamp = getattr(value, "timestamp", None)
    if callable(timestamp):
        return int(timestamp() * 1_000_000_000)
    return 0


def select_files(
    entries: list[tuple[int, str]],
    cursor: Optional[str],
) -> tuple[list[str], Optional[str]]:
    """Return keys to read and the cursor to store after a successful read.

    `entries` are `(mtime_ns, key)`. A legacy cursor (plain key, no
    version envelope) still admits lexicographically later keys, and also
    any key whose mtime is newer than the cursor file's mtime.
    """
    if not entries:
        return [], cursor

    parsed = decode_file_cursor(cursor) if cursor else None
    legacy = cursor if cursor and parsed is None else None
    legacy_mtime: Optional[int] = None
    if legacy is not None:
        for mtime_ns, key in entries:
            if key == legacy:
                legacy_mtime = mtime_ns
                break

    def include(mtime_ns: int, key: str) -> bool:
        if not cursor:
            return True
        if parsed is not None:
            return (mtime_ns, key) > parsed
        assert legacy is not None
        if key > legacy:
            return True
        return legacy_mtime is not None and mtime_ns > legacy_mtime

    chosen = sorted((mtime_ns, key) for mtime_ns, key in entries if include(mtime_ns, key))
    if not chosen:
        return [], cursor
    last_mtime, last_key = chosen[-1]
    return [key for _, key in chosen], encode_file_cursor(last_mtime, last_key)
