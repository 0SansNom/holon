"""Value Type constraint / value validation (create-time + read-time)."""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime
from typing import Any, Optional

from holon_common.urn import InvalidURNError, parse as parse_urn

from .lifecycle import REGISTRY_LIFECYCLE_STATUSES


BASE_TYPES = {
    "string", "integer", "double", "boolean", "date", "timestamp",
    "short", "byte", "long", "decimal", "float", "geopoint", "geoshape", "vector",
}

LIFECYCLE_STATUSES = REGISTRY_LIFECYCLE_STATUSES
FORMAT_REGEX_MATCH_MODES = {"full", "substring"}

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
        if format_regex:
            match_mode = value_type_row.get("format_regex_match") or "full"
            matched = (
                re.search(format_regex, value) is not None
                if match_mode == "substring"
                else re.fullmatch(format_regex, value) is not None
            )
            if not matched:
                mode_label = "substring" if match_mode == "substring" else "full"
                return (
                    f"{value!r} does not match {name!r}'s required format "
                    f"({format_regex!r}, {mode_label} match)"
                )

    for constraint in value_type_row.get("constraints") or []:
        error = _check_constraint(value, constraint, base_type, name)
        if error:
            return error
    return None

