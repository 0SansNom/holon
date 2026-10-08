"""Read-time derived properties: functions, link aggregates, and struct reducers."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from holon_common import Principal

from . import core, function_registry, ontology

logger = logging.getLogger("knowledge")


_ALLOWED_AGGREGATES = {"sum", "count", "avg", "min", "max", "collect_list", "collect_set"}
_MAX_LINK_AGGREGATE_HOPS = 3
_DEFAULT_COLLECT_LIMIT = 10


def _link_aggregate_path(rule: dict) -> list[str]:
    path = rule.get("path")
    return path if isinstance(path, list) else []


async def _compute_link_aggregate(
    rule: dict, object_type_name: str, row: dict, principal: Principal,
    *, relation_types: list[dict], authorized_types: set[str], property_mapping_cache: dict[str, dict],
    neighbor_property_mapping_cache: dict[str, dict],
) -> Optional[Any]:
    """A reducer over a 1–3 hop RelationType path:
    `count`/`sum`/`avg`/`min`/`max`/`collect_list`/`collect_set`.
    Reuses `_resolve_relation_neighbors` per hop — no separate fetch
    path. Returns `None` (property skipped) if the path, neighbor type,
    or aggregated property can't be resolved.
    """
    path = _link_aggregate_path(rule)
    if not path or len(path) > _MAX_LINK_AGGREGATE_HOPS or "id" not in row:
        return None

    frontier: list[tuple[str, dict]] = [(object_type_name, row)]
    for link_name in path:
        next_frontier: list[tuple[str, dict]] = []
        for current_type, current_row in frontier:
            relation = core._find_relation_by_link_name(relation_types, current_type, link_name)
            if relation is None or "id" not in current_row:
                continue
            result = await core._resolve_relation_neighbors(
                relation, current_type, current_row["id"], current_row, principal,
                authorized_types=authorized_types, property_mapping_cache=property_mapping_cache,
            )
            if result is None:
                continue
            neighbor_type, neighbor_rows, _direction = result
            for neighbor_row in neighbor_rows:
                next_frontier.append((neighbor_type, neighbor_row))
        frontier = next_frontier
        if not frontier:
            break

    neighbor_rows = [neighbor_row for _type, neighbor_row in frontier]
    aggregate = rule.get("aggregate")
    if aggregate == "count":
        return len(neighbor_rows)
    if not frontier:
        return None

    neighbor_type = frontier[0][0]
    neighbor_property = rule.get("property")
    neighbor_mapping = neighbor_property_mapping_cache.get(neighbor_type)
    if neighbor_mapping is None:
        neighbor_handle = await core._type_handle(neighbor_type, principal.tenant_id)
        if neighbor_handle is None:
            return None
        neighbor_definition = await ontology.get_object_type(core.pool, neighbor_handle["urn"])
        if neighbor_definition is None:
            return None
        neighbor_mapping = neighbor_definition["property_mapping"]
        neighbor_property_mapping_cache[neighbor_type] = neighbor_mapping
    neighbor_column = neighbor_mapping.get(neighbor_property)
    if neighbor_column is None:
        return None

    raw_values = [neighbor_row.get(neighbor_column) for neighbor_row in neighbor_rows]
    values = [v for v in raw_values if v is not None]

    if aggregate in ("collect_list", "collect_set"):
        limit = rule.get("collect_limit", _DEFAULT_COLLECT_LIMIT)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            limit = _DEFAULT_COLLECT_LIMIT
        if aggregate == "collect_set":
            seen: set[str] = set()
            unique: list[Any] = []
            for v in values:
                key = json.dumps(v, sort_keys=True, default=str)
                if key in seen:
                    continue
                seen.add(key)
                unique.append(v)
                if len(unique) >= limit:
                    break
            return unique
        return values[:limit]

    numeric = [float(v) for v in values]
    if not numeric:
        return None
    if aggregate == "sum":
        return sum(numeric)
    if aggregate == "avg":
        return sum(numeric) / len(numeric)
    if aggregate == "min":
        return min(numeric)
    if aggregate == "max":
        return max(numeric)
    return None


def _reduce_array(values: list, reducer: str, by: Optional[str]) -> Optional[Any]:
    """The struct-reducer's actual reduction — `first`/`last` are
    positional; `latest`/`max` and `earliest`/`min` compare either the
    raw element (`by` is `None`, a scalar array) or one of its fields
    (`by` set, a struct array). A `TypeError` from comparing
    incompatible/missing values propagates to the caller, which already
    treats a failure here as "skip this property for this row", not a
    crash — same contract every other derived-property path already has.
    """
    if reducer == "first":
        return values[0]
    if reducer == "last":
        return values[-1]
    key = (lambda v: v.get(by)) if by else (lambda v: v)
    if reducer in ("latest", "max"):
        return max(values, key=key)
    if reducer in ("earliest", "min"):
        return min(values, key=key)
    return None


def _compute_struct_reducer(rule: dict, object_type: dict, row: dict) -> Optional[Any]:
    """Derived property reducer — this one over
    one of *this* ObjectType's own array properties (struct array or
    scalar array), rather than a linked type's. The array value is
    already a parsed Python list by the time this runs: `_mask_and_derive`
    always runs `_coerce_property_types` before `_apply_derived_properties`,
    so a `struct`/`array`-kind property's JSON text is already real
    nested data here, not a string to re-parse.
    """
    array_property = rule.get("property")
    column = (object_type.get("property_mapping") or {}).get(array_property)
    if column is None:
        return None
    array_value = row.get(column)
    if not isinstance(array_value, list) or not array_value:
        return None
    return _reduce_array(array_value, rule.get("reducer"), rule.get("by"))


def _skip_derived_property(property_name: str, exc: BaseException) -> None:
    """One derived property failed. It stays off the row; the read stays 200.

    Function plugins do external I/O and can fail on a downstream outage.
    Link aggregates and struct reducers are isolated the same way.
    """
    logger.exception(
        "derived property %r failed, skipping it for this row",
        property_name,
        exc_info=exc,
    )


async def _apply_derived_properties(object_type_urn: str, rows: list[dict], principal: Principal) -> list[dict]:
    """Read-time computation of every `derived_properties` entry — a
    plain string is a Function plugin invocation (the original,
    unchanged shape); a `{"kind": "link_aggregate", ...}` dict is a
    reducer over a RelationType (`_compute_link_aggregate`); a
    `{"kind": "struct_reducer", ...}` dict is a reducer over one of this
    ObjectType's own array properties (`_compute_struct_reducer`) —
    The other two real "derived property" mechanisms. All three
    translate their inputs to *ontology* property names via `property_mapping`
    (not the raw source-column keys `resolver.py`/`serving_store.py`
    return — an ontology-level concept shouldn't need to know storage
    column names, except `struct_reducer`, which reads its own array
    property's already-parsed value directly off the row).
    If a Function's required input was masked to `None` by
    `_mask_confidential_properties`, that derived property is skipped
    entirely rather than computed from a missing value — never a
    misleading default silently leaking a shape of the masked data.
    A derived property that fails to compute (plugin error, missing or
    inactive Function) is left absent and named in `_failedDerivedFields`,
    so callers can tell a failure from an empty value.
    Plugin lookups happen once per declared derived property, not once
    per row; a `link_aggregate`'s RelationType registry is likewise
    fetched at most once per call, not once per row.
    """
    if not rows:
        return rows
    object_type = await ontology.get_object_type(core.pool, object_type_urn)
    derived = (object_type.get("derived_properties") or {}) if object_type else {}
    if not derived:
        return rows
    property_mapping = object_type["property_mapping"]
    object_type_name = object_type_urn.rsplit(":", 1)[-1]

    function_entries = {name: value for name, value in derived.items() if isinstance(value, str)}
    link_aggregate_entries = {
        name: value for name, value in derived.items() if isinstance(value, dict) and value.get("kind") == "link_aggregate"
    }
    struct_reducer_entries = {
        name: value for name, value in derived.items() if isinstance(value, dict) and value.get("kind") == "struct_reducer"
    }

    resolved: dict[str, tuple[dict, Any]] = {}
    unresolved: list[str] = []
    for property_name, function_name in function_entries.items():
        registration = await function_registry.find_active_function_by_name(core.pool, function_name)
        if registration is not None:
            resolved[property_name] = (registration, function_registry.load_function_plugin(registration["manifest"]))
        else:
            logger.warning(
                "derived property %r: no active function %r for %s, skipping it",
                property_name, function_name, object_type_urn,
            )
            unresolved.append(property_name)

    relation_types = await ontology.list_relation_types(core.pool, principal.tenant_id) if link_aggregate_entries else []
    authorized_types = {object_type_name}
    property_mapping_cache: dict[str, dict] = {}
    neighbor_property_mapping_cache: dict[str, dict] = {}

    result_rows = []
    for row in rows:
        row = dict(row)
        failed = list(unresolved)
        translated = {camel: row.get(source_col) for camel, source_col in property_mapping.items()}
        for property_name, (registration, plugin) in resolved.items():
            required = (registration["manifest"].get("input_schema") or {}).get("required", [])
            if any(translated.get(field) is None for field in required):
                continue
            try:
                output = await plugin.call(**translated)
            except Exception as exc:
                _skip_derived_property(property_name, exc)
                failed.append(property_name)
                continue
            if isinstance(output, dict) and property_name in output:
                row[property_name] = output[property_name]
        # Through core: replacing core._compute_link_aggregate or
        # core._compute_struct_reducer replaces what runs for the row.
        for property_name, rule in link_aggregate_entries.items():
            try:
                value = await core._compute_link_aggregate(
                    rule, object_type_name, row, principal,
                    relation_types=relation_types, authorized_types=authorized_types,
                    property_mapping_cache=property_mapping_cache,
                    neighbor_property_mapping_cache=neighbor_property_mapping_cache,
                )
            except Exception as exc:
                _skip_derived_property(property_name, exc)
                failed.append(property_name)
                continue
            if value is not None:
                row[property_name] = value
        for property_name, rule in struct_reducer_entries.items():
            try:
                value = core._compute_struct_reducer(rule, object_type, row)
            except Exception as exc:
                _skip_derived_property(property_name, exc)
                failed.append(property_name)
                continue
            if value is not None:
                row[property_name] = value
        if failed:
            row["_failedDerivedFields"] = failed
        result_rows.append(row)
    return result_rows
