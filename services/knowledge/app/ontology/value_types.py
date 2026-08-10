"""Value Type registry — reusable, named *data* types (not display
formatting; see `object_types.py`'s `property_formats` for that
separate, pre-existing concern). A Value Type is a base primitive plus
optional constraints — the same semantic-type idea Foundry calls Value
Types.

Base types cover every scalar Holon can genuinely back today (string,
the numeric-width family, date/timestamp, boolean, geopoint/geoshape as
plain string/JSON representations, vector as a plain number array).
Deliberately excludes Foundry base types that would need real
infrastructure this build doesn't have — attachment/media reference
(blob storage wiring through Actions), time series/geotemporal series
(no time-series store), cipher (no field-level encryption feature).
Adding those as selectable-but-unbacked types would be a decorative gap,
not a real one closed.

Constraints (`enum`/`range`/`rid`/`uuid`) are the ones that map onto a
*single scalar value* — Foundry's remaining two (`uniqueness`/`nested`)
are array-shaped and don't belong here: Holon declares array-ness at the
*property* level (`object_types.py`'s `property_types`, `{"kind":
"array", "element": {...}}`), where the element is already itself a
Value Type/Shared Property Type reference carrying its own constraints —
so per-element validation is already free, structurally, without a
separate "nested" constraint kind.

Two real call sites: (1) `object_types.py`'s `property_types` (a typed
property references a Value Type by name, validated at publish time —
see `publishing.py`'s `_validate_property_types`), (2) `action_types.py`'s
declarative Action parameters (validated at invocation time via
`validate_value` below) — the same registry, two consumers, one
vocabulary, not duplicated.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime
from typing import Any, Optional

import asyncpg
from holon_common.urn import InvalidURNError, parse as parse_urn

BASE_TYPES = {
    "string", "integer", "double", "boolean", "date", "timestamp",
    "short", "byte", "long", "decimal", "float", "geopoint", "geoshape", "vector",
}

_INT_LIKE = {"integer", "short", "byte", "long"}
_FLOAT_LIKE = {"double", "decimal", "float"}
_NUMERIC = _INT_LIKE | _FLOAT_LIKE
# What `range` can meaningfully compare: numeric types directly, date/
# timestamp (ISO-8601 strings compare correctly with plain `<`/`>` since
# `_check_base_type` already validated them via `fromisoformat`), string
# as a length constraint.
_RANGE_APPLICABLE = _NUMERIC | {"date", "timestamp", "string"}

_ALLOWED_CONSTRAINT_KINDS = {"enum", "range", "rid", "uuid"}

_GEOPOINT_RE = re.compile(r"^-?\d+(\.\d+)?,-?\d+(\.\d+)?$")
_GEOJSON_TYPES = {"Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon", "GeometryCollection"}


def _validate_constraints(base_type: str, constraints: list) -> None:
    """Structural check at creation time — same tier `format_regex`
    already gets (compiled once, up front) rather than only discovering a
    malformed or nonsensical constraint the first time `validate_value`
    happens to be called against real data.
    """
    for index, constraint in enumerate(constraints):
        if not isinstance(constraint, dict):
            raise ValueError(f"constraint #{index} must be an object")
        kind = constraint.get("kind")
        if kind not in _ALLOWED_CONSTRAINT_KINDS:
            raise ValueError(f"constraint #{index}: unknown kind {kind!r} (expected one of {sorted(_ALLOWED_CONSTRAINT_KINDS)})")
        if kind == "enum":
            values = constraint.get("values")
            if not isinstance(values, list) or not values:
                raise ValueError(f"constraint #{index}: 'enum' requires a non-empty 'values' list")
        elif kind == "range":
            if base_type not in _RANGE_APPLICABLE:
                raise ValueError(f"constraint #{index}: 'range' isn't meaningful for base_type {base_type!r}")
            if "min" not in constraint and "max" not in constraint:
                raise ValueError(f"constraint #{index}: 'range' requires 'min' and/or 'max'")
        elif kind in ("rid", "uuid"):
            if base_type != "string":
                raise ValueError(f"constraint #{index}: {kind!r} only applies to base_type='string'")


async def create_value_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    base_type: str,
    format_regex: Optional[str] = None,
    constraints: Optional[list[dict]] = None,
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
    constraints = constraints or []
    _validate_constraints(base_type, constraints)

    await pool.execute(
        """
        INSERT INTO value_type (tenant_id, name, base_type, format_regex, constraints, description)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6)
        """,
        tenant_id, name, base_type, format_regex, json.dumps(constraints), description,
    )
    return await get_value_type(pool, tenant_id, name)


async def update_value_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    format_regex: Optional[str] = None,
    constraints: Optional[list[dict]] = None,
    description: Optional[str] = None,
) -> dict:
    """Partial update — `base_type` and `name` are deliberately not
    accepted params at all: changing the base type would invalidate
    every value already validated against this Value Type. `None` means
    "leave unchanged"; re-runs the same structural validation
    `create_value_type` does against the *existing* `base_type`.
    """
    current = await get_value_type(pool, tenant_id, name)
    if current is None:
        raise ValueError(f"unknown value type: {name!r}")

    new_format_regex = current["format_regex"] if format_regex is None else format_regex
    new_constraints = current["constraints"] if constraints is None else constraints
    new_description = current["description"] if description is None else description

    if new_format_regex is not None and current["base_type"] != "string":
        raise ValueError(f"format_regex is only meaningful for base_type='string', not {current['base_type']!r}")
    if new_format_regex is not None:
        try:
            re.compile(new_format_regex)
        except re.error as exc:
            raise ValueError(f"format_regex is not a valid regular expression: {exc}") from exc
    _validate_constraints(current["base_type"], new_constraints)

    await pool.execute(
        """
        UPDATE value_type SET format_regex = $1, constraints = $2::jsonb, description = $3
        WHERE tenant_id = $4 AND name = $5
        """,
        new_format_regex, json.dumps(new_constraints), new_description, tenant_id, name,
    )
    return await get_value_type(pool, tenant_id, name)


def _parse_row(row: asyncpg.Record) -> dict:
    result = dict(row)
    if isinstance(result.get("constraints"), str):
        result["constraints"] = json.loads(result["constraints"])
    return result


async def get_value_type(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM value_type WHERE tenant_id = $1 AND name = $2", tenant_id, name)
    return _parse_row(row) if row else None


async def list_value_types(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM value_type WHERE tenant_id = $1 ORDER BY name", tenant_id)
    return [_parse_row(row) for row in rows]


def _check_base_type(value: Any, base_type: str, name: str) -> Optional[str]:
    if base_type == "string":
        if not isinstance(value, str):
            return f"{name!r} expects a string, got {type(value).__name__}"
        return None
    if base_type in _INT_LIKE:
        # bool is a subclass of int in Python — an actual boolean must
        # never silently pass an integer check.
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{name!r} expects an integer, got {type(value).__name__}"
        return None
    if base_type in _FLOAT_LIKE:
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
    if base_type == "geopoint":
        # Foundry's own documented representation: comma-separated
        # "latitude,longitude" — kept identical rather than inventing a
        # Holon-specific shape for no reason.
        if not isinstance(value, str) or not _GEOPOINT_RE.match(value):
            return f"{name!r} expects a 'lat,lng' geopoint string, got {value!r}"
        return None
    if base_type == "geoshape":
        if not isinstance(value, str):
            return f"{name!r} expects a GeoJSON geometry string, got {type(value).__name__}"
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return f"{name!r} expects a valid GeoJSON geometry, got invalid JSON"
        if not isinstance(parsed, dict) or parsed.get("type") not in _GEOJSON_TYPES:
            return f"{name!r} expects a GeoJSON geometry with a recognized 'type' (one of {sorted(_GEOJSON_TYPES)})"
        return None
    if base_type == "vector":
        if not isinstance(value, list) or not value or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
            return f"{name!r} expects a non-empty array of numbers"
        return None
    return f"unknown base_type {base_type!r}"  # unreachable given create_value_type's own validation


def _check_constraint(value: Any, constraint: dict, base_type: str, name: str) -> Optional[str]:
    kind = constraint.get("kind")
    if kind == "enum":
        values = constraint.get("values", [])
        if constraint.get("caseSensitive", True):
            ok = value in values
        else:
            ok = isinstance(value, str) and value.lower() in {str(v).lower() for v in values}
        return None if ok else f"{value!r} is not one of {name!r}'s allowed values {values}"
    if kind == "range":
        subject = len(value) if base_type == "string" else value
        minimum, maximum = constraint.get("min"), constraint.get("max")
        if minimum is not None and subject < minimum:
            return f"{value!r} is below {name!r}'s minimum ({minimum})"
        if maximum is not None and subject > maximum:
            return f"{value!r} is above {name!r}'s maximum ({maximum})"
        return None
    if kind == "rid":
        try:
            parse_urn(value)
        except InvalidURNError:
            return f"{value!r} is not a valid resource identifier for {name!r}"
        return None
    if kind == "uuid":
        try:
            uuid.UUID(str(value))
        except ValueError:
            return f"{value!r} is not a valid UUID for {name!r}"
        return None
    return None  # unreachable given create_value_type's own validation


def validate_value(value: Any, value_type_row: dict) -> Optional[str]:
    """Pure function: `None` means valid, otherwise a human-readable
    reason. Shared by `publishing.py` (structural-only, at publish time —
    it never has an actual data value to check, only the declaration
    itself) and `actions.py` (real values, at Action-invocation time —
    the point where a Value Type's constraints are actually enforced
    against real data, not just declared).
    """
    base_type = value_type_row["base_type"]
    name = value_type_row["name"]

    type_error = _check_base_type(value, base_type, name)
    if type_error:
        return type_error

    if base_type == "string":
        format_regex = value_type_row.get("format_regex")
        if format_regex and not re.fullmatch(format_regex, value):
            return f"{value!r} does not match {name!r}'s required format ({format_regex!r})"

    for constraint in value_type_row.get("constraints") or []:
        error = _check_constraint(value, constraint, base_type, name)
        if error:
            return error
    return None
