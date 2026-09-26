"""File sync resumes on (mtime, key), not lexicographic key alone."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app.file_cursor import decode_file_cursor, select_files  # noqa: E402


def test_same_mtime_later_key_is_not_skipped() -> None:
    first_keys, cursor = select_files([(1_000, "b.txt")], None)
    assert first_keys == ["b.txt"]
    assert decode_file_cursor(cursor) == (1_000, "b.txt")

    keys, cursor = select_files([(1_000, "b.txt"), (1_000, "c.txt")], cursor)
    assert keys == ["c.txt"]
    assert decode_file_cursor(cursor) == (1_000, "c.txt")

    keys, again = select_files([(1_000, "b.txt"), (1_000, "c.txt")], cursor)
    assert keys == []
    assert again == cursor


def test_modified_or_out_of_order_name_is_picked_up_by_mtime() -> None:
    _, cursor = select_files([(1_000, "m.txt")], None)
    # Uploaded after `m.txt` but sorts first; rewritten `a.txt` is newer.
    keys, cursor = select_files(
        [(1_000, "m.txt"), (2_000, "a.txt"), (1_500, "z-late.txt")],
        cursor,
    )
    assert keys == ["z-late.txt", "a.txt"]
    body = json.loads(cursor or "")
    assert body["key"] == "a.txt"
    assert body["mtime_ns"] == 2_000


def test_legacy_key_cursor_still_admits_later_names_and_newer_mtimes() -> None:
    keys, cursor = select_files(
        [(1_000, "m.txt"), (500, "z.txt"), (2_000, "a.txt")],
        "m.txt",
    )
    assert keys == ["z.txt", "a.txt"]
    assert decode_file_cursor(cursor) == (2_000, "a.txt")
