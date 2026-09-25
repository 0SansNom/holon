"""Tests for SQL incremental-cursor value coercion (`_bind_cursor_value`).

Covers int/float coercion (unchanged) plus ISO-8601 date/datetime parsing so
asyncpg can bind cursor values against `timestamp`/`timestamptz` columns
instead of receiving a bare string.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parents[3]
sys.modules.setdefault("asyncpg", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app.sql_source_registry import _bind_cursor_value  # noqa: E402


def test_bind_cursor_value_coerces_integers() -> None:
    assert _bind_cursor_value("42") == 42
    assert isinstance(_bind_cursor_value("42"), int)
    assert _bind_cursor_value("-7") == -7


def test_bind_cursor_value_coerces_floats() -> None:
    assert _bind_cursor_value("3.14") == 3.14
    assert isinstance(_bind_cursor_value("3.14"), float)


def test_bind_cursor_value_parses_date_only() -> None:
    result = _bind_cursor_value("2024-01-01")
    assert result == datetime.datetime(2024, 1, 1)
    assert result.tzinfo is None


def test_bind_cursor_value_parses_naive_datetime() -> None:
    result = _bind_cursor_value("2024-01-01T12:34:56")
    assert result == datetime.datetime(2024, 1, 1, 12, 34, 56)
    assert result.tzinfo is None


def test_bind_cursor_value_parses_datetime_with_z_as_utc() -> None:
    result = _bind_cursor_value("2024-01-01T12:34:56Z")
    assert result == datetime.datetime(2024, 1, 1, 12, 34, 56, tzinfo=datetime.timezone.utc)
    assert result.tzinfo is not None


def test_bind_cursor_value_parses_datetime_with_numeric_offset() -> None:
    result = _bind_cursor_value("2024-01-01T12:34:56+02:00")
    expected_tz = datetime.timezone(datetime.timedelta(hours=2))
    assert result == datetime.datetime(2024, 1, 1, 12, 34, 56, tzinfo=expected_tz)
    assert result.tzinfo is not None


def test_bind_cursor_value_parses_datetime_with_fractional_seconds() -> None:
    result = _bind_cursor_value("2024-01-01T12:34:56.789Z")
    assert result == datetime.datetime(
        2024, 1, 1, 12, 34, 56, 789000, tzinfo=datetime.timezone.utc
    )


def test_bind_cursor_value_leaves_invalid_date_shape_as_string() -> None:
    # Shape matches YYYY-MM-DD but month=13 is not a valid date.
    assert _bind_cursor_value("2024-13-99") == "2024-13-99"


def test_bind_cursor_value_leaves_plain_strings_as_strings() -> None:
    assert _bind_cursor_value("some-opaque-id") == "some-opaque-id"
    assert _bind_cursor_value("abc123") == "abc123"
