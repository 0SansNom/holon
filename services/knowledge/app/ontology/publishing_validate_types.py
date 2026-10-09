"""Property-type and project-scope checks at ObjectType publish time."""
from __future__ import annotations

from typing import Optional

import asyncpg
import httpx

from .publishing_validate_formats import (
    _ALLOWED_PROPERTY_TYPE_KINDS,
    _ALLOWED_RENDER_HINTS,
    _validate_property_control_metadata,
    _validate_struct_field_metadata,
)


async def _validate_property_types(
    pool: asyncpg.Pool, *, tenant_id: str, property_mapping: dict, derived_properties: dict[str, str], property_types: dict[str, dict]
) -> None:
    """Enforced at publish time, same tier as `_validate_property_formats`
    (a genuinely separate concern — data typing, not display formatting):
    a property_types entry must name a real property, a known `kind`,
    and — for `value_type`/`shared_property_type` — a real, registered
    reference. Nesting is checked structurally, one hop deep, with a
    single named exception: an array's element may be a `struct` (that
    struct's own fields are then leaves-only, matching standard
    "struct array" shape) — every other nested position (a plain
    struct's own field, or that struct-array-element's own field) stays
    restricted to `value_type`/`shared_property_type`. No storage change
    needed for this — `core.py`'s `_parse_struct_or_array` already
    `json.loads`s any array shape generically.

    A top-level entry may also carry `editable`/`required`/`visibility`
    (property control), plus `render_hints` (list of
    searchable/sortable/selectable/identifier) and `type_classes`
    (lowercase identifier strings) — checked structurally here
    (well-formed, and only ever on a top-level entry), enforced against
    real Action edits in `actions/declarative.py`'s
    `request_generic_action`. Render hints also drive unified-search
    indexing (`search.index_rows`).
    """
    from . import shared_property_types as shared_property_types_module
    from . import value_types as value_types_module

    known_properties = set(property_mapping) | set(derived_properties)

    async def _validate_leaf(
        property_name: str,
        rule: dict,
        *,
        in_struct: bool = False,
        in_array: bool = False,
        allow_field_column: bool = True,
    ) -> None:
        nested = in_struct or in_array
        kind = rule.get("kind")
        _validate_property_control_metadata(
            property_name, rule, nested=nested, allow_field_column=allow_field_column and in_struct and not in_array
        )
        # Metadata-only entry (visibility / editable / required / hints without a typed kind).
        if kind is None:
            if nested:
                raise ValueError(f"property_types entry for {property_name!r}: nested fields require a kind")
            return
        if kind not in _ALLOWED_PROPERTY_TYPE_KINDS:
            raise ValueError(f"property_types entry for {property_name!r} has unknown kind {kind!r} (expected one of {sorted(_ALLOWED_PROPERTY_TYPE_KINDS)})")
        if kind == "value_type":
            value_type_name = rule.get("value_type")
            if await value_types_module.get_value_type(pool, tenant_id, value_type_name) is None:
                raise ValueError(f"property_types entry for {property_name!r} names unknown value_type {value_type_name!r}")
            return
        if kind == "shared_property_type":
            shared_property_type_name = rule.get("shared_property_type")
            spt = await shared_property_types_module.get_shared_property_type(pool, tenant_id, shared_property_type_name)
            if spt is None:
                raise ValueError(f"property_types entry for {property_name!r} names unknown shared_property_type {shared_property_type_name!r}")
            # Nesting is one level deep everywhere else in this function
            # (struct/array can't contain another struct) — a struct-typed
            # SPT referenced as a leaf inside a local struct would smuggle
            # that same nesting in by reference, and deleting the SPT later
            # has no way to preserve a struct shape at a leaf position
            # (`shared_property_types._detach_spt_from_rule` only ever
            # rewrites a leaf to a scalar value_type).
            if in_struct and isinstance(spt.get("struct_properties"), dict) and spt["struct_properties"]:
                raise ValueError(
                    f"property_types entry for {property_name!r}: shared_property_type "
                    f"{shared_property_type_name!r} is struct-typed, which can't be nested inside "
                    f"another struct (struct-in-struct is forbidden even by reference)"
                )
            return
        if kind == "struct":
            # A bare top-level struct is fine; a struct as an array's
            # element is the one deliberate exception (in_array=True,
            # in_struct=False here). Either way, its own fields go one
            # level deeper (in_struct=True) — a struct can never contain
            # another struct, so `in_struct` already being True is the
            # one thing that blocks this branch.
            if in_struct:
                raise ValueError(f"property_types entry for {property_name!r}: struct/array nesting is limited to one level")
            nested_properties = rule.get("properties")
            if not isinstance(nested_properties, dict) or not nested_properties:
                raise ValueError(f"property_types entry for {property_name!r}: 'struct' requires a non-empty 'properties' dict")
            # Per-field dataset columns only on top-level structs (Column mapping).
            field_columns_ok = not in_array
            for nested_name, nested_rule in nested_properties.items():
                await _validate_leaf(
                    f"{property_name}.{nested_name}",
                    nested_rule,
                    in_struct=True,
                    in_array=in_array,
                    allow_field_column=field_columns_ok,
                )
            return
        if kind == "array":
            if nested:
                raise ValueError(f"property_types entry for {property_name!r}: struct/array nesting is limited to one level")
            element_rule = rule.get("element")
            if not isinstance(element_rule, dict):
                raise ValueError(f"property_types entry for {property_name!r}: 'array' requires an 'element' type rule")
            if "unique_elements" in rule and not isinstance(rule.get("unique_elements"), bool):
                raise ValueError(
                    f"property_types entry for {property_name!r}: unique_elements must be a boolean"
                )
            await _validate_leaf(f"{property_name}[]", element_rule, in_array=True, allow_field_column=False)

    for property_name, rule in property_types.items():
        if property_name not in known_properties:
            raise ValueError(f"property_types entry for {property_name!r} names a property this ObjectType doesn't have")
        await _validate_leaf(property_name, rule)

async def _validate_project_scope(*, identity_url: str, project_urn: str, identity_token: str) -> None:
    """Enforced at publish time, same tier as the other validations.
    Knowledge never reads Identity's database directly — same
    cross-service boundary this build keeps everywhere else (Qdrant
    indexing calls Knowledge's HTTP API rather than its Postgres, the
    exact same reasoning applies here in reverse).
    """
    local_name = project_urn.rsplit(":", 1)[-1]
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{identity_url}/projects/{local_name}", headers={"Authorization": f"Bearer {identity_token}"}
        )
    if response.status_code == 404:
        raise ValueError(f"unknown project: {project_urn!r}")
    response.raise_for_status()

