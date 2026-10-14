"""Inclusive cursor resume: tied values are ingested once."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app.cursor_window import advance_cursor, lookback_value  # noqa: E402


def test_tied_timestamp_second_row_is_kept_exactly_once() -> None:
    first = [{"id": 1, "updated_at": "2024-01-01T00:00:00Z", "name": "a"}]
    opened = advance_cursor(first, cursor_property="updated_at", last_cursor=None, boundary_keys=None)
    assert [row["id"] for row in opened.rows] == [1]
    assert opened.cursor == "2024-01-01T00:00:00Z"
    assert opened.changed

    second_batch = [
        {"id": 1, "updated_at": "2024-01-01T00:00:00Z", "name": "a"},
        {"id": 2, "updated_at": "2024-01-01T00:00:00Z", "name": "b"},
    ]
    resumed = advance_cursor(
        second_batch,
        cursor_property="updated_at",
        last_cursor=opened.cursor,
        boundary_keys=opened.boundary_keys,
    )
    assert [row["id"] for row in resumed.rows] == [2]
    assert resumed.cursor == opened.cursor
    assert resumed.changed

    third = advance_cursor(
        second_batch,
        cursor_property="updated_at",
        last_cursor=resumed.cursor,
        boundary_keys=resumed.boundary_keys,
    )
    assert third.rows == []
    assert third.changed is False


def test_older_lookback_rows_are_dropped() -> None:
    seen = advance_cursor(
        [{"id": 8, "name": "last"}],
        cursor_property="id",
        last_cursor=None,
        boundary_keys=None,
    )
    again = advance_cursor(
        [{"id": 1, "name": "old"}, {"id": 8, "name": "last"}, {"id": 9, "name": "new"}],
        cursor_property="id",
        last_cursor=seen.cursor,
        boundary_keys=seen.boundary_keys,
    )
    assert [row["id"] for row in again.rows] == [9]
    assert again.cursor == "9"


def test_fractional_timestamp_is_newer_than_a_whole_second() -> None:
    advanced = advance_cursor(
        [{"id": 2, "updated_at": "2024-06-01T10:00:00.001Z"}],
        cursor_property="updated_at",
        last_cursor="2024-06-01T10:00:00Z",
        boundary_keys=None,
    )
    assert [row["id"] for row in advanced.rows] == [2]
    assert advanced.cursor == "2024-06-01T10:00:00.001Z"


def test_boundary_keys_are_capped() -> None:
    from app.cursor_window import _MAX_BOUNDARY_KEYS

    rows = [{"id": i, "updated_at": "2024-06-01T10:00:00Z"} for i in range(_MAX_BOUNDARY_KEYS + 10)]
    advanced = advance_cursor(rows, cursor_property="updated_at", last_cursor=None, boundary_keys=None)
    import json
    assert len(json.loads(advanced.boundary_keys)) == _MAX_BOUNDARY_KEYS


def test_lookback_steps_behind_int_and_timestamp() -> None:
    assert lookback_value("8") == "7"
    assert lookback_value("2024-01-01T00:00:01Z") == "2024-01-01T00:00:00Z"
    assert lookback_value("opaque-token") == "opaque-token"
