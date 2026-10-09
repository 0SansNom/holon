"""Confidential-field masking and read-time struct coercion."""

from __future__ import annotations

from typing import Any

from holon_common import Principal

from . import core, ontology
from .struct_values import assemble_struct_value, parse_struct_or_array


async def _mask_confidential_properties(
    object_type_urn: str, principal: Principal, rows: list[dict], *, key_prefix: str = ""
) -> list[dict]:
    """Row/column security enforcement point. A confidential
    property is replaced with `None` (and named in `_maskedFields`) rather
    than the whole object being withheld. Visibility is
    `policy.confidential_visible` — the same live OPA check search uses.

    `key_prefix`: `execute_plan`'s `join` operation returns rows
    with every column prefixed `s_`/`t_` to keep same-named columns from
    two different ObjectTypes from colliding — this masks
    `{key_prefix}{confidential_column}` instead of the bare column name,
    called once per side with each side's own classifications, same
    function either way.
    """
    from . import policy

    if await policy.confidential_visible(principal):
        return rows
    property_classifications = await ontology.get_property_classifications(core.pool, object_type_urn)
    confidential_properties = {name for name, classification in property_classifications.items() if classification == "confidential"}
    if not confidential_properties:
        return rows

    masked_rows = []
    for row in rows:
        row = dict(row)
        masked_fields = [
            f"{key_prefix}{name}" for name in confidential_properties if row.get(f"{key_prefix}{name}") is not None
        ]
        for name in masked_fields:
            row[name] = None
        if masked_fields:
            row.setdefault("_maskedFields", [])
            row["_maskedFields"] = row["_maskedFields"] + masked_fields
        masked_rows.append(row)
    return masked_rows


def _parse_struct_or_array(property_name: str, rule: dict, raw_value: Any) -> Any:
    """Compatibility wrapper — see ``struct_values.parse_struct_or_array``."""
    return parse_struct_or_array(rule, raw_value)


async def _coerce_property_types(object_type_urn: str, rows: list[dict]) -> list[dict]:
    """Read-time struct/array parsing for `property_types` (see
    `0000_baseline.sql` / `object_type.property_types`) — operates on the row's
    *raw* source-column keys, the same keys `_resolve_one`/`_resolve_many`
    already return unchanged for every ordinary property (property_mapping
    is a declared/checked contract, not a runtime rename — see
    `_apply_derived_properties`'s own docstring for why that translation
    only ever happens internally, just for a Function's inputs).
    """
    if not rows:
        return rows
    object_type = await ontology.get_object_type(core.pool, object_type_urn)
    property_types = (object_type.get("property_types") or {}) if object_type else {}
    structured = {name: rule for name, rule in property_types.items() if rule.get("kind") in ("struct", "array")}
    if not structured:
        return rows
    property_mapping = object_type["property_mapping"]

    result_rows = []
    for row in rows:
        row = dict(row)
        for property_name, rule in structured.items():
            source_col = property_mapping.get(property_name)
            if rule.get("kind") == "struct":
                assembled = assemble_struct_value(rule, row, source_col)
                if assembled is not None and source_col is not None:
                    row[source_col] = assembled
                elif assembled is not None and source_col is None:
                    row[property_name] = assembled
                continue
            if source_col is None or source_col not in row:
                continue
            row[source_col] = _parse_struct_or_array(property_name, rule, row[source_col])
        result_rows.append(row)
    return result_rows


async def _mask_and_derive(object_type_urn: str, principal: Principal, rows: list[dict]) -> list[dict]:
    """The combined read choke point: property masking, then struct/array
    type coercion, then derived properties — masking first since a
    derived property must never see a value the principal itself
    couldn't see unmasked; coercion before derivation so a Function
    declared against a structured property receives the real parsed
    shape, not a raw JSON string.
    """
    masked = await _mask_confidential_properties(object_type_urn, principal, rows)
    coerced = await _coerce_property_types(object_type_urn, masked)
    return await core._apply_derived_properties(object_type_urn, coerced, principal)


async def _filter_by_instance_markings(
    object_type_urn: str, tenant_id: str, principal: Principal, rows: list[dict]
) -> list[dict]:
    """Instance-level markings — the other attachment point
    alongside ObjectType-wide `markings` (already enforced earlier, in
    `_authorize_object_type`, before any row is even fetched). A row
    carrying an instance marking the principal doesn't hold is dropped
    entirely rather than masked — a marking is a coarse clearance gate,
    not a per-property one, so "denied" means the row doesn't exist for
    this principal, the same treatment ReBAC-denied resources already
    get elsewhere in this build (a filtered-out row surfaces as an empty
    list entry or, via `_resolve_one`, a 404 — never a 403 buried inside
    a 200 list).
    """
    if not rows:
        return rows
    instance_ids = [str(row["id"]) for row in rows]
    markings_by_instance = await ontology.get_instance_markings_bulk(
        core.pool, object_type_urn=object_type_urn, tenant_id=tenant_id, instance_ids=instance_ids
    )
    if not markings_by_instance:
        return rows
    result = []
    for row in rows:
        instance_markings = markings_by_instance.get(str(row["id"]))
        if instance_markings and not await core._authorize_markings(principal, instance_markings):
            continue
        result.append(row)
    return result
