"""Runtime validation of property values against `property_types` rules.

Publish-time validation (`publishing._validate_property_types`) only
checks that declarations are well-formed. Action edits need the same
shape rules applied to real values — struct/array nesting included —
so a malformed JSON payload cannot land in `object_instance_edit`.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import asyncpg

from . import shared_property_types as shared_property_types_module
from . import value_types as value_types_module


def _coerce_jsonish(value: Any) -> Any:
    """Actions sometimes receive struct/array payloads as JSON text."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
    return value


TypeCache = dict[tuple[str, str], Optional[dict]]


async def _cached_get_value_type(
    pool: asyncpg.Pool, tenant_id: str, name: Optional[str], cache: Optional[TypeCache]
) -> Optional[dict]:
    if cache is None:
        return await value_types_module.get_value_type(pool, tenant_id, name)
    key = ("value_type", name or "")
    if key not in cache:
        cache[key] = await value_types_module.get_value_type(pool, tenant_id, name)
    return cache[key]


async def _cached_get_shared_property_type(
    pool: asyncpg.Pool, tenant_id: str, name: Optional[str], cache: Optional[TypeCache]
) -> Optional[dict]:
    if cache is None:
        return await shared_property_types_module.get_shared_property_type(pool, tenant_id, name)
    key = ("shared_property_type", name or "")
    if key not in cache:
        cache[key] = await shared_property_types_module.get_shared_property_type(pool, tenant_id, name)
    return cache[key]


async def _validate_leaf(
    pool: asyncpg.Pool,
    tenant_id: str,
    property_name: str,
    rule: dict,
    value: Any,
    cache: Optional[TypeCache] = None,
) -> Optional[str]:
    kind = rule.get("kind")
    if kind == "value_type":
        value_type = await _cached_get_value_type(pool, tenant_id, rule.get("value_type"), cache)
        if value_type is None:
            return f"{property_name!r} references unknown value_type {rule.get('value_type')!r}"
        return value_types_module.validate_value(value, value_type)
    if kind == "shared_property_type":
        spt = await _cached_get_shared_property_type(pool, tenant_id, rule.get("shared_property_type"), cache)
        if spt is None:
            return f"{property_name!r} references unknown shared_property_type {rule.get('shared_property_type')!r}"
        struct_properties = spt.get("struct_properties")
        if isinstance(struct_properties, dict) and struct_properties:
            return await validate_typed_property_value(
                pool,
                tenant_id,
                {"kind": "struct", "properties": struct_properties},
                value,
                property_name=property_name,
                cache=cache,
            )
        value_type = await _cached_get_value_type(pool, tenant_id, spt["value_type"], cache)
        if value_type is None:
            return f"{property_name!r}: shared property {spt['api_name']!r} wraps unknown value_type"
        return value_types_module.validate_value(value, value_type)
    return f"{property_name!r}: unsupported nested kind {kind!r}"


async def validate_typed_property_value(
    pool: asyncpg.Pool,
    tenant_id: str,
    rule: dict,
    value: Any,
    *,
    property_name: str,
    cache: Optional[TypeCache] = None,
    allow_unknown_struct_fields: bool = False,
) -> Optional[str]:
    """Return None when valid, otherwise a human-readable error.

    `cache`: pass the same dict across many calls (e.g. one per row of a
    sync) to resolve each distinct Value Type / Shared Property Type once
    instead of once per property per row — omit for a one-off call.

    `allow_unknown_struct_fields`: when True (materialize / health / index
    paths), extra keys in a JSON struct blob are ignored and only declared
    fields are validated. Action writes keep the default False so callers
    cannot sneak undeclared fields into edits.
    """
    if value is None:
        return None

    kind = rule.get("kind")
    if kind is None:
        return None

    value = _coerce_jsonish(value)

    if kind == "value_type" or kind == "shared_property_type":
        return await _validate_leaf(pool, tenant_id, property_name, rule, value, cache)

    if kind == "struct":
        if not isinstance(value, dict):
            return f"{property_name!r} expects a struct object, got {type(value).__name__}"
        properties = rule.get("properties") or {}
        if not isinstance(properties, dict) or not properties:
            return f"{property_name!r} has an empty struct declaration"
        unknown = set(value) - set(properties)
        if unknown and not allow_unknown_struct_fields:
            return f"{property_name!r} has unknown struct field(s): {sorted(unknown)}"
        for field_name, field_rule in properties.items():
            if field_name not in value:
                continue
            if not isinstance(field_rule, dict):
                return f"{property_name}.{field_name} has an invalid type rule"
            error = await _validate_leaf(
                pool, tenant_id, f"{property_name}.{field_name}", field_rule, value[field_name], cache
            )
            if error:
                return error
        return None

    if kind == "array":
        if not isinstance(value, list):
            return f"{property_name!r} expects an array, got {type(value).__name__}"
        if rule.get("unique_elements"):
            seen: list[str] = []
            for item in value:
                key = json.dumps(item, sort_keys=True, default=str)
                if key in seen:
                    return f"{property_name!r} requires unique elements"
                seen.append(key)
        element = rule.get("element")
        if not isinstance(element, dict):
            return f"{property_name!r} has an invalid array element rule"
        for index, item in enumerate(value):
            item_name = f"{property_name}[{index}]"
            if element.get("kind") == "struct":
                error = await validate_typed_property_value(
                    pool,
                    tenant_id,
                    element,
                    item,
                    property_name=item_name,
                    cache=cache,
                    allow_unknown_struct_fields=allow_unknown_struct_fields,
                )
            else:
                error = await _validate_leaf(pool, tenant_id, item_name, element, item, cache)
            if error:
                return error
        return None

    return None


async def validate_object_row(
    pool: asyncpg.Pool,
    tenant_id: str,
    *,
    property_mapping: dict[str, str],
    property_types: dict[str, dict],
    row: dict,
    cache: Optional[TypeCache] = None,
) -> list[str]:
    """Validate one instance row against ObjectType `property_types`.

    Values are read from the *source column* keys in `property_mapping`
    (same shape as Iceberg / serving-store rows). Returns a list of
    human-readable errors (empty = valid).

    `cache`: pass the same dict across every row of a multi-row caller
    (`partition_rows_by_property_types`, a health-check sample loop) so
    each distinct Value Type / Shared Property Type referenced in
    `property_types` is fetched once for the whole batch, not once per
    row — omit for a genuinely one-off single-row validation.

    Undeclared struct keys from source JSON are tolerated here (projected
    away on read); Action edit validation stays strict.
    """
    if not property_types:
        return []
    errors: list[str] = []
    for property_name, rule in property_types.items():
        if not isinstance(rule, dict) or not rule.get("kind"):
            continue
        source_col = property_mapping.get(property_name)
        if source_col is None or source_col not in row:
            continue
        error = await validate_typed_property_value(
            pool,
            tenant_id,
            rule,
            row.get(source_col),
            property_name=property_name,
            cache=cache,
            allow_unknown_struct_fields=True,
        )
        if error:
            errors.append(error)
    return errors


async def validate_value_type_casts(
    pool: asyncpg.Pool,
    tenant_id: str,
    *,
    casts: dict[str, str],
    row: dict,
    row_index: int = 0,
) -> list[dict]:
    """Pipeline Builder-style logical type cast: each column → Value Type.

    Returns structured errors `{row_index, column, detail}` (empty = ok).
    Unknown Value Type names are errors (fail closed at cast time).
    """
    errors: list[dict] = []
    for column, value_type_name in casts.items():
        if column not in row:
            continue
        value_type = await value_types_module.get_value_type(pool, tenant_id, value_type_name)
        if value_type is None:
            errors.append(
                {
                    "row_index": row_index,
                    "column": column,
                    "detail": f"unknown value_type {value_type_name!r}",
                }
            )
            continue
        value = row.get(column)
        if value is None:
            continue
        detail = value_types_module.validate_value(value, value_type)
        if detail:
            errors.append({"row_index": row_index, "column": column, "detail": detail})
    return errors


async def partition_rows_by_property_types(
    pool: asyncpg.Pool,
    tenant_id: str,
    *,
    property_mapping: dict[str, str],
    property_types: dict[str, dict],
    rows: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Split rows into (valid_for_index, invalid) using property_types.

    Foundry parity for "OT fails to index when values fail Value Type
    validation": callers index only the valid partition; invalid rows stay
    in the serving store for repair, and health check surfaces samples.
    """
    if not property_types or not rows:
        return list(rows), []
    valid: list[dict] = []
    invalid: list[dict] = []
    # One cache for the whole batch: property_types names only a handful
    # of distinct Value Types/Shared Property Types, however many rows
    # reference them — resolve each once instead of once per row.
    cache: TypeCache = {}
    for row in rows:
        errors = await validate_object_row(
            pool,
            tenant_id,
            property_mapping=property_mapping,
            property_types=property_types,
            row=row,
            cache=cache,
        )
        if errors:
            invalid.append(row)
        else:
            valid.append(row)
    return valid, invalid
