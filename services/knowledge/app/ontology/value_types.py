"""Value Type registry — reusable, named *data* types (not display
formatting; see `object_types.py`'s `property_formats` for that
separate, pre-existing concern). A Value Type is a base primitive
(string/integer/double/boolean/date/timestamp) plus an optional format
constraint (a regex, only meaningful for `base_type="string"`, e.g.
"Email" = string + `^[^@]+@[^@]+\\.[^@]+$`) — the same semantic-type
idea Foundry calls Value Types, deliberately narrow: no units, no
custom validation code, just what a closed, checkable vocabulary needs.

Two real call sites: (1) `object_types.py`'s `property_types` (a typed
property references a Value Type by name, validated at publish time —
see `publishing.py`'s `_validate_property_types`), (2) `action_types.py`'s
declarative Action parameters (validated at invocation time via
`validate_value` below) — the same registry, two consumers, one
vocabulary, not duplicated.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

import asyncpg

BASE_TYPES = {"string", "integer", "double", "boolean", "date", "timestamp"}


async def create_value_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    base_type: str,
    format_regex: Optional[str] = None,
    description: str = "",
) -> dict:
    if base_type not in BASE_TYPES:
        raise ValueError(f"unknown base_type: {base_type!r} (expected one of {sorted(BASE_TYPES)})")
    if format_regex is not None and base_type != "string":
        raise ValueError(f"format_regex is only meaningful for base_type='string', not {base_type!r}")
    if format_regex is not None:
        try:
            re.compile(format_regex)
        except re.error as exc:
            raise ValueError(f"format_regex is not a valid regular expression: {exc}") from exc

    await pool.execute(
        """
        INSERT INTO value_type (tenant_id, name, base_type, format_regex, description)
        VALUES ($1, $2, $3, $4, $5)
        """,
        tenant_id, name, base_type, format_regex, description,
    )
    return await get_value_type(pool, tenant_id, name)


async def get_value_type(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM value_type WHERE tenant_id = $1 AND name = $2", tenant_id, name)
    return dict(row) if row else None


async def list_value_types(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM value_type WHERE tenant_id = $1 ORDER BY name", tenant_id)
    return [dict(row) for row in rows]


def validate_value(value: Any, value_type_row: dict) -> Optional[str]:
    """Pure function: `None` means valid, otherwise a human-readable
    reason. Shared by `publishing.py` (structural-only, at publish time —
    it never has an actual data value to check, only the declaration
    itself) and `actions.py` (real values, at Action-invocation time —
    the point where a Value Type's format constraint is actually
    enforced against real data, not just declared).
    """
    base_type = value_type_row["base_type"]
    name = value_type_row["name"]

    if base_type == "string":
        if not isinstance(value, str):
            return f"{name!r} expects a string, got {type(value).__name__}"
        format_regex = value_type_row.get("format_regex")
        if format_regex and not re.fullmatch(format_regex, value):
            return f"{value!r} does not match {name!r}'s required format ({format_regex!r})"
        return None
    if base_type == "integer":
        # bool is a subclass of int in Python — an actual boolean must
        # never silently pass an integer check.
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{name!r} expects an integer, got {type(value).__name__}"
        return None
    if base_type == "double":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{name!r} expects a number, got {type(value).__name__}"
        return None
    if base_type == "boolean":
        if not isinstance(value, bool):
            return f"{name!r} expects a boolean, got {type(value).__name__}"
        return None
    if base_type in ("date", "timestamp"):
        if not isinstance(value, str):
            return f"{name!r} expects an ISO-8601 {base_type} string, got {type(value).__name__}"
        try:
            (date if base_type == "date" else datetime).fromisoformat(value)
        except ValueError:
            return f"{value!r} is not a valid ISO-8601 {base_type} for {name!r}"
        return None
    return f"unknown base_type {base_type!r}"  # unreachable given create_value_type's own validation
