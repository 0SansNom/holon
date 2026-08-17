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

Constraints (`enum`/`range`/`rid`/`uuid`) apply to a single scalar
value. Foundry's array `uniqueness` lives on Holon's *property-level*
array rule (`property_types` `unique_elements: true`) because Holon
declares array-ness there — not as a Value Type base type. Element
constraints are free via the array's `element` Value Type / SPT ref
(Foundry's "nested"). String `format_regex` may match full value or
substring (`format_regex_match`).

Versioning (Foundry parity): `name` is the stable registry key that
property_types / Action params reference. Metadata (`description`,
`api_name`, `display_name`, `example_value`, `lifecycle_status`,
`project_urn`) may change without bumping `version`. Changing
`format_regex` / `format_regex_match` / `constraints` archives the
previous snapshot into `value_type_revision` and increments `version` —
consumers keep resolving by `name` and therefore auto-receive the
latest constraints (Foundry's non-breaking propagate). `base_type`
stays immutable.

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
from holon_common import build_urn
from holon_common.urn import InvalidURNError, parse as parse_urn

from .lifecycle import REGISTRY_LIFECYCLE_STATUSES, normalize_deprecation_metadata

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


def value_type_urn(tenant_id: str, name: str) -> str:
    """Stable Holon RID — `hl:{tenant}:global:value-type:{name}`."""
    return build_urn(tenant_id, "global", "value-type", name)


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


def _normalize_api_name(api_name: Optional[str], name: str) -> str:
    cleaned = (api_name or "").strip() or name
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", cleaned):
        raise ValueError(
            f"api_name {cleaned!r} must start with a letter and contain only letters, digits, underscores"
        )
    return cleaned


def _constraints_equal(a: Optional[list], b: Optional[list]) -> bool:
    return json.dumps(a or [], sort_keys=True) == json.dumps(b or [], sort_keys=True)


async def _insert_revision(conn: asyncpg.Connection, row: dict) -> None:
    await conn.execute(
        """
        INSERT INTO value_type_revision (
            tenant_id, name, version, base_type, format_regex, constraints,
            description, api_name, display_name, example_value, lifecycle_status,
            format_regex_match
        ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11, $12)
        ON CONFLICT (tenant_id, name, version) DO NOTHING
        """,
        row["tenant_id"],
        row["name"],
        row["version"],
        row["base_type"],
        row.get("format_regex"),
        json.dumps(row.get("constraints") or []),
        row.get("description") or "",
        row.get("api_name") or row["name"],
        row.get("display_name") or "",
        row.get("example_value"),
        row.get("lifecycle_status") or "experimental",
        row.get("format_regex_match") or "full",
    )


async def _fetch_value_type(conn: asyncpg.Connection, tenant_id: str, name: str) -> Optional[dict]:
    row = await conn.fetchrow("SELECT * FROM value_type WHERE tenant_id = $1 AND name = $2", tenant_id, name)
    return _parse_row(row) if row else None


async def create_value_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    base_type: str,
    format_regex: Optional[str] = None,
    constraints: Optional[list[dict]] = None,
    description: str = "",
    api_name: Optional[str] = None,
    display_name: str = "",
    example_value: Optional[str] = None,
    lifecycle_status: str = "experimental",
    format_regex_match: str = "full",
    project_urn: Optional[str] = None,
    deprecation_reason: Optional[str] = None,
    deprecation_deadline=None,
    replacement_urn: Optional[str] = None,
) -> dict:
    if base_type not in BASE_TYPES:
        raise ValueError(f"unknown base_type: {base_type!r} (expected one of {sorted(BASE_TYPES)})")
    dep = normalize_deprecation_metadata(
        lifecycle_status,
        deprecation_reason=deprecation_reason,
        deprecation_deadline=deprecation_deadline,
        replacement_urn=replacement_urn,
    )
    if format_regex_match not in FORMAT_REGEX_MATCH_MODES:
        raise ValueError(f"unknown format_regex_match: {format_regex_match!r} (expected full|substring)")
    if format_regex is not None and base_type != "string":
        raise ValueError(f"format_regex is only meaningful for base_type='string', not {base_type!r}")
    if format_regex is not None:
        try:
            re.compile(format_regex)
        except re.error as exc:
            raise ValueError(f"format_regex is not a valid regular expression: {exc}") from exc
    constraints = constraints or []
    _validate_constraints(base_type, constraints)
    resolved_api_name = _normalize_api_name(api_name, name)
    scoped_project = project_urn.strip() if isinstance(project_urn, str) and project_urn.strip() else None

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO value_type (
                    tenant_id, name, base_type, format_regex, constraints, description,
                    api_name, display_name, example_value, version, lifecycle_status,
                    format_regex_match, project_urn,
                    deprecation_reason, deprecation_deadline, replacement_urn
                ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, 1, $10, $11, $12, $13, $14, $15)
                """,
                tenant_id,
                name,
                base_type,
                format_regex,
                json.dumps(constraints),
                description,
                resolved_api_name,
                display_name or name,
                example_value,
                dep["lifecycle_status"],
                format_regex_match,
                scoped_project,
                dep["deprecation_reason"],
                dep["deprecation_deadline"],
                dep["replacement_urn"],
            )
            created = await _fetch_value_type(conn, tenant_id, name)
            assert created is not None
            await _insert_revision(conn, created)
    return created


async def update_value_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    format_regex: Optional[str] = None,
    constraints: Optional[list[dict]] = None,
    description: Optional[str] = None,
    api_name: Optional[str] = None,
    display_name: Optional[str] = None,
    example_value: Optional[str] = None,
    lifecycle_status: Optional[str] = None,
    clear_example_value: bool = False,
    format_regex_match: Optional[str] = None,
    project_urn: Optional[str] = None,
    clear_project_urn: bool = False,
    deprecation_reason: Optional[str] = None,
    deprecation_deadline=None,
    replacement_urn: Optional[str] = None,
) -> dict:
    """Partial update — `base_type` and `name` are deliberately not
    accepted params at all: changing the base type would invalidate
    every value already validated against this Value Type. `None` means
    "leave unchanged".

    Foundry: changing constraints/format creates a new version; metadata
    edits do not. Holon bumps `version` and archives the prior snapshot
    when constraint payload, format_regex, or format_regex_match changes.
    """
    current = await get_value_type(pool, tenant_id, name)
    if current is None:
        raise ValueError(f"unknown value type: {name!r}")

    # None = leave unchanged; "" = clear (UI/API send empty string to drop a regex).
    if format_regex is None:
        new_format_regex = current["format_regex"]
    elif format_regex == "":
        new_format_regex = None
    else:
        new_format_regex = format_regex
    new_constraints = current["constraints"] if constraints is None else constraints
    new_description = current["description"] if description is None else description
    new_api_name = (
        current.get("api_name") or current["name"]
        if api_name is None
        else _normalize_api_name(api_name, name)
    )
    new_display_name = current.get("display_name") or current["name"] if display_name is None else display_name
    if clear_example_value:
        new_example = None
    elif example_value is None:
        new_example = current.get("example_value")
    else:
        new_example = example_value
    new_lifecycle = current.get("lifecycle_status") or "experimental"
    if lifecycle_status is not None:
        new_lifecycle = lifecycle_status
    new_dep_reason = (
        current.get("deprecation_reason") if deprecation_reason is None else deprecation_reason
    )
    new_dep_deadline = (
        current.get("deprecation_deadline") if deprecation_deadline is None else deprecation_deadline
    )
    new_replacement = current.get("replacement_urn") if replacement_urn is None else replacement_urn
    dep = normalize_deprecation_metadata(
        new_lifecycle,
        deprecation_reason=new_dep_reason,
        deprecation_deadline=new_dep_deadline,
        replacement_urn=new_replacement,
    )
    new_match = current.get("format_regex_match") or "full"
    if format_regex_match is not None:
        if format_regex_match not in FORMAT_REGEX_MATCH_MODES:
            raise ValueError(f"unknown format_regex_match: {format_regex_match!r} (expected full|substring)")
        new_match = format_regex_match
    if clear_project_urn:
        new_project_urn: Optional[str] = None
    elif project_urn is not None:
        new_project_urn = project_urn.strip() or None
    else:
        new_project_urn = current.get("project_urn")

    if new_format_regex is not None and current["base_type"] != "string":
        raise ValueError(f"format_regex is only meaningful for base_type='string', not {current['base_type']!r}")
    if new_format_regex is not None:
        try:
            re.compile(new_format_regex)
        except re.error as exc:
            raise ValueError(f"format_regex is not a valid regular expression: {exc}") from exc
    _validate_constraints(current["base_type"], new_constraints)

    constraint_changed = (
        (new_format_regex != current.get("format_regex"))
        or (new_match != (current.get("format_regex_match") or "full"))
        or not _constraints_equal(new_constraints, current.get("constraints"))
    )
    new_version = int(current.get("version") or 1) + (1 if constraint_changed else 0)

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Ensure the live row's current version is archived before bumping.
            if constraint_changed:
                await _insert_revision(conn, current)
            await conn.execute(
                """
                UPDATE value_type SET
                    format_regex = $1,
                    constraints = $2::jsonb,
                    description = $3,
                    api_name = $4,
                    display_name = $5,
                    example_value = $6,
                    version = $7,
                    lifecycle_status = $8,
                    format_regex_match = $9,
                    project_urn = $10,
                    deprecation_reason = $11,
                    deprecation_deadline = $12,
                    replacement_urn = $13
                WHERE tenant_id = $14 AND name = $15
                """,
                new_format_regex,
                json.dumps(new_constraints),
                new_description,
                new_api_name,
                new_display_name,
                new_example,
                new_version,
                dep["lifecycle_status"],
                new_match,
                new_project_urn,
                dep["deprecation_reason"],
                dep["deprecation_deadline"],
                dep["replacement_urn"],
                tenant_id,
                name,
            )
            updated = await _fetch_value_type(conn, tenant_id, name)
            assert updated is not None
            if constraint_changed:
                await _insert_revision(conn, updated)
    return updated


def _parse_row(row: asyncpg.Record) -> dict:
    result = dict(row)
    if isinstance(result.get("constraints"), str):
        result["constraints"] = json.loads(result["constraints"])
    if not result.get("api_name"):
        result["api_name"] = result["name"]
    if not result.get("display_name"):
        result["display_name"] = result["name"]
    result.setdefault("version", 1)
    result.setdefault("lifecycle_status", "experimental")
    result.setdefault("format_regex_match", "full")
    result["urn"] = value_type_urn(result["tenant_id"], result["name"])
    return result


async def get_value_type(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM value_type WHERE tenant_id = $1 AND name = $2", tenant_id, name)
    return _parse_row(row) if row else None


async def list_value_types(
    pool: asyncpg.Pool, tenant_id: str, *, include_deprecated: bool = True
) -> list[dict]:
    if include_deprecated:
        rows = await pool.fetch("SELECT * FROM value_type WHERE tenant_id = $1 ORDER BY name", tenant_id)
    else:
        rows = await pool.fetch(
            "SELECT * FROM value_type WHERE tenant_id = $1 AND lifecycle_status <> 'deprecated' ORDER BY name",
            tenant_id,
        )
    return [_parse_row(row) for row in rows]


async def list_value_type_revisions(pool: asyncpg.Pool, tenant_id: str, name: str) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT * FROM value_type_revision
        WHERE tenant_id = $1 AND name = $2
        ORDER BY version DESC
        """,
        tenant_id,
        name,
    )
    return [_parse_row(row) for row in rows]


async def deprecate_value_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    deprecation_reason: str,
    deprecation_deadline,
    replacement_urn: Optional[str] = None,
) -> dict:
    return await update_value_type(
        pool,
        tenant_id=tenant_id,
        name=name,
        lifecycle_status="deprecated",
        deprecation_reason=deprecation_reason,
        deprecation_deadline=deprecation_deadline,
        replacement_urn=replacement_urn,
    )


async def delete_value_type(pool: asyncpg.Pool, *, tenant_id: str, name: str) -> bool:
    """Hard delete — used for SpiceDB seed compensation; prefer deprecate otherwise."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM value_type_revision WHERE tenant_id = $1 AND name = $2",
                tenant_id,
                name,
            )
            result = await conn.execute(
                "DELETE FROM value_type WHERE tenant_id = $1 AND name = $2",
                tenant_id,
                name,
            )
    return result.endswith("1")


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
