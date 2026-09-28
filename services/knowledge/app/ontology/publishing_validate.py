"""Structural checks for ObjectType versions, run before a draft is published."""

from __future__ import annotations

import json
import re
import uuid
from typing import Optional

import asyncpg
import httpx

from holon_common import EventActor, EventEnvelope, build_urn, outbox

from . import markings as markings_module
from .object_types import get_object_type, get_object_type_version, validate_ot_metadata
from .type_classes import normalize_type_classes
from .render_hints import ALLOWED_RENDER_HINTS, normalize_render_hints


def _action_local_name(action_name: str) -> str:
    """Interface `required_actions` store the short local name
    (e.g. `archive`); registry keys are usually `Type.action`.
    Bare names (no dot) pass through unchanged.
    """
    return action_name.split(".", 1)[1] if "." in action_name else action_name


async def _actions_available_on_object_type(
    pool: asyncpg.Pool, *, tenant_id: str, object_type_name: str, implements: list[str]
) -> set[str]:
    """Short action names invokable on this ObjectType after publish —
    declarative ActionTypes targeting the OT directly, and ActionTypes
    targeting any interface this version declares in `implements`
    (Actions-on-interfaces).
    """
    from .action_types import list_action_types

    implements_set = set(implements)
    names: set[str] = set()

    for action_type in await list_action_types(pool, tenant_id):
        if action_type.get("target_object_type") == object_type_name:
            names.add(_action_local_name(action_type["name"]))
        target_interface = action_type.get("target_interface")
        if target_interface and target_interface in implements_set:
            names.add(_action_local_name(action_type["name"]))
    return names


async def _validate_implements(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    object_type_name: str,
    property_mapping: dict,
    implements: list[str],
    property_types: Optional[dict[str, dict]] = None,
    link_constraint_bindings: Optional[dict] = None,
    interface_property_bindings: Optional[dict] = None,
    interface_overrides: Optional[dict[str, dict]] = None,
) -> None:
    """Validate that the version satisfies all declared interface contracts."""
    from .interfaces import (
        effective_interface_contract,
        expand_implements,
        outbound_cardinality_from_relation,
        property_type_binding_key,
        relation_other_endpoint,
        resolve_interface_property_path,
    )
    from .object_types import list_object_types
    from .relation_types import list_relation_types

    expanded_implements = await expand_implements(pool, tenant_id, implements)
    object_action_names = await _actions_available_on_object_type(
        pool,
        tenant_id=tenant_id,
        object_type_name=object_type_name,
        implements=list(expanded_implements),
    )
    ot_property_types = property_types or {}
    bindings_by_interface = link_constraint_bindings or {}
    prop_bindings_by_interface = interface_property_bindings or {}
    relations = await list_relation_types(pool, tenant_id)
    relations_by_name = {rel["name"]: rel for rel in relations}
    object_types_by_name = {ot["name"]: ot for ot in await list_object_types(pool, tenant_id)}

    for interface_name in implements:
        interface = await effective_interface_contract(
            pool, tenant_id, interface_name, overrides=interface_overrides,
        )
        prop_bindings = prop_bindings_by_interface.get(interface_name) or {}
        if prop_bindings and not isinstance(prop_bindings, dict):
            raise ValueError(
                f"{object_type_name} cannot implement {interface_name!r}: "
                f"interface_property_bindings[{interface_name!r}] must be an object"
            )
        missing_properties: list[str] = []
        for prop_name in interface["required_properties"]:
            try:
                resolve_interface_property_path(
                    property_mapping=property_mapping,
                    property_types=ot_property_types,
                    interface_prop=prop_name,
                    binding_path=prop_bindings.get(prop_name),
                )
            except ValueError:
                missing_properties.append(prop_name)
        if missing_properties:
            raise ValueError(
                f"{object_type_name} cannot implement {interface_name!r}: "
                f"missing required propert{'y' if len(missing_properties) == 1 else 'ies'} {missing_properties}"
            )
        missing_actions = [a for a in interface["required_actions"] if a not in object_action_names]
        if missing_actions:
            raise ValueError(
                f"{object_type_name} cannot implement {interface_name!r}: "
                f"missing required action{'s' if len(missing_actions) != 1 else ''} {missing_actions}"
            )
        for prop_name, iface_rule in (interface.get("property_types") or {}).items():
            expected = property_type_binding_key(iface_rule)
            if expected is None:
                continue
            try:
                _, ot_rule = resolve_interface_property_path(
                    property_mapping=property_mapping,
                    property_types=ot_property_types,
                    interface_prop=prop_name,
                    binding_path=prop_bindings.get(prop_name),
                )
            except ValueError as exc:
                raise ValueError(
                    f"{object_type_name} cannot implement {interface_name!r}: {exc}"
                ) from exc
            actual = property_type_binding_key(ot_rule) if ot_rule is not None else None
            if actual != expected:
                kind, ref = expected
                raise ValueError(
                    f"{object_type_name} cannot implement {interface_name!r}: "
                    f"property {prop_name!r} must be typed as {kind} {ref!r} "
                    f"(got {actual!r})"
                )

        iface_bindings = bindings_by_interface.get(interface_name) or {}
        if not isinstance(iface_bindings, dict):
            raise ValueError(
                f"{object_type_name} cannot implement {interface_name!r}: "
                f"link_constraint_bindings[{interface_name!r}] must be an object"
            )
        for constraint in interface.get("link_constraints") or []:
            api_name = constraint["api_name"]
            relation_name = iface_bindings.get(api_name)
            if not relation_name:
                if constraint.get("required"):
                    raise ValueError(
                        f"{object_type_name} cannot implement {interface_name!r}: "
                        f"missing required link binding for {api_name!r}"
                    )
                continue
            relation = relations_by_name.get(relation_name)
            if relation is None:
                raise ValueError(
                    f"{object_type_name} cannot implement {interface_name!r}: "
                    f"link {api_name!r} binds unknown RelationType {relation_name!r}"
                )
            outbound = outbound_cardinality_from_relation(relation, object_type_name)
            if outbound is None:
                raise ValueError(
                    f"{object_type_name} cannot implement {interface_name!r}: "
                    f"RelationType {relation_name!r} does not touch this ObjectType"
                )
            if outbound != constraint["cardinality"]:
                raise ValueError(
                    f"{object_type_name} cannot implement {interface_name!r}: "
                    f"link {api_name!r} requires cardinality {constraint['cardinality']!r}, "
                    f"RelationType {relation_name!r} is outbound {outbound!r}"
                )
            other = relation_other_endpoint(relation, object_type_name)
            if constraint["target_kind"] == "object_type":
                if other != constraint["target"]:
                    raise ValueError(
                        f"{object_type_name} cannot implement {interface_name!r}: "
                        f"link {api_name!r} requires target ObjectType {constraint['target']!r}, "
                        f"got {other!r}"
                    )
            else:
                other_ot = object_types_by_name.get(other or "")
                other_implements = await expand_implements(
                    pool, tenant_id, (other_ot or {}).get("implements") or [],
                )
                if constraint["target"] not in other_implements:
                    raise ValueError(
                        f"{object_type_name} cannot implement {interface_name!r}: "
                        f"link {api_name!r} requires target interface {constraint['target']!r}, "
                        f"but {other!r} does not implement it"
                    )


async def assert_interface_tighten_compatible(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    interface_name: str,
    previous_properties: list[str],
    previous_actions: list[str],
    new_properties: list[str],
    new_actions: list[str],
    previous_property_types: Optional[dict] = None,
    new_property_types: Optional[dict] = None,
    previous_link_constraints: Optional[list] = None,
    new_link_constraints: Optional[list] = None,
    previous_parent_interfaces: Optional[list] = None,
    new_parent_interfaces: Optional[list] = None,
) -> None:
    """Refuse edits that would break published implementers of this
    interface or of any child that extends it (effective contract).
    """
    from .interfaces import (
        ancestor_interface_names,
        link_constraints_tighten,
        property_types_tighten,
    )
    from .object_types import list_object_types

    added_properties = set(new_properties) - set(previous_properties)
    added_actions = set(new_actions) - set(previous_actions)
    types_tighten = property_types_tighten(
        previous_property_types or {}, new_property_types or {}
    )
    links_tighten = link_constraints_tighten(
        previous_link_constraints or [], new_link_constraints or []
    )
    prev_parents = list(previous_parent_interfaces or [])
    next_parents = list(new_parent_interfaces or [])
    parents_tighten = set(next_parents) - set(prev_parents)
    if (
        not added_properties
        and not added_actions
        and not types_tighten
        and not links_tighten
        and not parents_tighten
    ):
        return

    proposed = {
        "parent_interfaces": next_parents,
        "required_properties": new_properties,
        "required_actions": new_actions,
        "property_types": new_property_types or {},
        "link_constraints": new_link_constraints or [],
    }
    overrides = {interface_name: proposed}

    failures: list[str] = []
    for object_type in await list_object_types(pool, tenant_id):
        implements = object_type.get("implements") or []
        affected = False
        for impl in implements:
            if impl == interface_name:
                affected = True
                break
            if interface_name in await ancestor_interface_names(pool, tenant_id, impl):
                affected = True
                break
        if not affected:
            continue
        try:
            await _validate_implements(
                pool,
                tenant_id=tenant_id,
                object_type_name=object_type["name"],
                property_mapping=object_type.get("property_mapping") or {},
                implements=implements,
                property_types=object_type.get("property_types") or {},
                link_constraint_bindings=object_type.get("link_constraint_bindings") or {},
                interface_property_bindings=object_type.get("interface_property_bindings") or {},
                interface_overrides=overrides,
            )
        except ValueError as exc:
            failures.append(str(exc))

    if failures:
        raise ValueError(
            f"cannot tighten interface {interface_name!r}: "
            f"{len(failures)} implementer(s) would break — " + "; ".join(failures)
        )


_ALLOWED_AGGREGATES = {"sum", "count", "avg", "min", "max", "collect_list", "collect_set"}
_ALLOWED_STRUCT_REDUCERS = {"first", "last", "latest", "earliest", "max", "min"}
_FIELD_BASED_STRUCT_REDUCERS = {"latest", "earliest", "max", "min"}
_MAX_LINK_AGGREGATE_HOPS = 3


def _find_relation_by_link_name(relation_types: list[dict], object_type_name: str, link_name: object) -> Optional[dict]:
    """Pure structural lookup — same matching `core._find_relation_by_link_name`
    does at read/traversal time, duplicated here rather than imported
    because `ontology/` never depends on the app-layer `core` module (the
    reverse dependency direction every other cross-layer boundary in this
    build already keeps). No live traversal at publish time, just "does a
    RelationType named this exist, touching this ObjectType" — the same
    split every other real-reference check in this function already has.
    """
    for relation in relation_types:
        source_name = relation["source_object_type_urn"].rsplit(":", 1)[-1]
        target_name = relation["target_object_type_urn"].rsplit(":", 1)[-1]
        local_name = relation["name"].split(".", 1)[-1]
        forward = (relation.get("source_api_name") or "").strip() or local_name
        reverse = (relation.get("target_api_name") or "").strip() or relation.get("target_property")
        if source_name == object_type_name and forward == link_name:
            return relation
        if target_name == object_type_name and reverse == link_name:
            return relation
        if source_name == object_type_name and local_name == link_name:
            return relation
        if target_name == object_type_name and relation.get("target_property") == link_name:
            return relation
    return None


def _link_aggregate_path(rule: dict) -> list[str]:
    """Return list of link names along multi-hop path."""
    path = rule.get("path")
    return path if isinstance(path, list) else []


async def _validate_derived_properties(
    pool: asyncpg.Pool, *, derived_properties: dict[str, object], object_type_name: str, tenant_id: str,
    property_types: Optional[dict[str, dict]] = None,
) -> None:
    """Validate derived property definitions, aggregations, and reducers."""
    from .. import function_registry
    from .relation_types import list_relation_types

    property_types = property_types or {}
    relation_types: Optional[list[dict]] = None
    for property_name, value in derived_properties.items():
        if isinstance(value, str):
            plugin = await function_registry.find_active_function_by_name(pool, value)
            if plugin is None:
                raise ValueError(
                    f"derived property {property_name!r} names {value!r}, "
                    f"which is not a registered, active Function plugin"
                )
            continue

        if not isinstance(value, dict) or value.get("kind") not in ("link_aggregate", "struct_reducer"):
            raise ValueError(
                f"derived property {property_name!r} must be either a Function plugin name (string), "
                f"a {{'kind': 'link_aggregate', ...}} object, or a {{'kind': 'struct_reducer', ...}} object"
            )

        if value["kind"] == "struct_reducer":
            array_property = value.get("property")
            array_rule = property_types.get(array_property) if array_property else None
            if array_rule is None or array_rule.get("kind") != "array":
                raise ValueError(
                    f"derived property {property_name!r} names {array_property!r}, which isn't an 'array'-kind "
                    f"property_types entry on this ObjectType"
                )
            reducer = value.get("reducer")
            if reducer not in _ALLOWED_STRUCT_REDUCERS:
                raise ValueError(
                    f"derived property {property_name!r}: unknown reducer {reducer!r} "
                    f"(expected one of {sorted(_ALLOWED_STRUCT_REDUCERS)})"
                )
            element_rule = array_rule.get("element") or {}
            by = value.get("by")
            if reducer in _FIELD_BASED_STRUCT_REDUCERS:
                if element_rule.get("kind") == "struct":
                    if not by or by not in (element_rule.get("properties") or {}):
                        raise ValueError(
                            f"derived property {property_name!r}: reducer {reducer!r} on a struct array requires "
                            f"'by' to name one of the struct's own fields"
                        )
                elif by is not None:
                    raise ValueError(
                        f"derived property {property_name!r}: reducer {reducer!r} on a scalar array must not set "
                        f"'by' — there's nothing to key by, the values are compared directly"
                    )
            continue

        aggregate = value.get("aggregate")
        if aggregate not in _ALLOWED_AGGREGATES:
            raise ValueError(
                f"derived property {property_name!r}: unknown aggregate {aggregate!r} "
                f"(expected one of {sorted(_ALLOWED_AGGREGATES)})"
            )
        if "collect_limit" in value:
            limit = value["collect_limit"]
            if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
                raise ValueError(
                    f"derived property {property_name!r}: collect_limit must be a positive integer"
                )
        path = _link_aggregate_path(value)
        if (
            not path
            or len(path) > _MAX_LINK_AGGREGATE_HOPS
            or not all(isinstance(hop, str) and hop for hop in path)
        ):
            raise ValueError(
                f"derived property {property_name!r}: link_aggregate requires 'path' "
                f"(1–{_MAX_LINK_AGGREGATE_HOPS} link names)"
            )
        if relation_types is None:
            relation_types = await list_relation_types(pool, tenant_id)
        current_type = object_type_name
        related_urn: Optional[str] = None
        for hop in path:
            relation = _find_relation_by_link_name(relation_types, current_type, hop)
            if relation is None:
                raise ValueError(
                    f"derived property {property_name!r} names unknown relation {hop!r} "
                    f"from ObjectType {current_type!r}"
                )
            source_name = relation["source_object_type_urn"].rsplit(":", 1)[-1]
            if source_name == current_type:
                related_urn = relation["target_object_type_urn"]
            else:
                related_urn = relation["source_object_type_urn"]
            current_type = related_urn.rsplit(":", 1)[-1]
        if aggregate != "count":
            related_property = value.get("property")
            if not related_property:
                raise ValueError(f"derived property {property_name!r}: aggregate {aggregate!r} requires a 'property'")
            assert related_urn is not None
            related_definition = await get_object_type(pool, related_urn)
            if related_definition is None or related_property not in related_definition["property_mapping"]:
                raise ValueError(
                    f"derived property {property_name!r}: {related_property!r} is not a mapped property "
                    f"on the related ObjectType"
                )


_ALLOWED_FORMAT_KINDS = {"currency", "badge", "numeric", "datetime", "principal", "resource-link"}
_ALLOWED_BADGE_COLORS = {"primary", "success", "warning", "danger", "none"}
_ALLOWED_NUMERIC_STYLES = {"decimal", "currency", "percent", "unit"}
_ALLOWED_NUMERIC_NOTATIONS = {"standard", "compact", "scientific", "engineering"}
_NUMERIC_INT_FIELDS = (
    "minimumFractionDigits", "maximumFractionDigits",
    "minimumSignificantDigits", "maximumSignificantDigits", "minimumIntegerDigits",
)
_ALLOWED_DATETIME_STYLES = {"date", "datetime-long", "datetime-short", "iso8601", "relative", "time"}

_ALLOWED_RESOURCE_LINK_TYPES = {"object-type", "application"}


def _validate_property_formats(
    *, property_mapping: dict, derived_properties: dict[str, str], property_formats: dict[str, dict]
) -> None:
    """Enforced at publish time, same tier as `_validate_derived_properties`:
    a format rule must name a real property (mapped or derived) and a
    known `kind` with a well-formed rule body — not just any JSON blob a
    caller happened to send.
    """
    known_properties = set(property_mapping) | set(derived_properties)
    for property_name, rule in property_formats.items():
        if property_name not in known_properties:
            raise ValueError(f"format rule for {property_name!r} names a property this ObjectType doesn't have")
        kind = rule.get("kind")
        if kind not in _ALLOWED_FORMAT_KINDS:
            raise ValueError(f"format rule for {property_name!r} has unknown kind {kind!r} (expected one of {sorted(_ALLOWED_FORMAT_KINDS)})")
        if kind == "currency":
            if not isinstance(rule.get("currency"), str) or len(rule["currency"]) != 3:
                raise ValueError(f"format rule for {property_name!r}: 'currency' must be a 3-letter code (e.g. 'USD')")
        elif kind == "badge":
            colors = rule.get("colors")
            if not isinstance(colors, dict) or not colors:
                raise ValueError(f"format rule for {property_name!r}: 'colors' must be a non-empty value->color mapping")
            bad_colors = {c for c in colors.values() if c not in _ALLOWED_BADGE_COLORS}
            if bad_colors:
                raise ValueError(f"format rule for {property_name!r}: unknown badge color(s) {bad_colors} (expected one of {sorted(_ALLOWED_BADGE_COLORS)})")
        elif kind == "numeric":
            style = rule.get("style", "decimal")
            if style not in _ALLOWED_NUMERIC_STYLES:
                raise ValueError(f"format rule for {property_name!r}: unknown numeric style {style!r} (expected one of {sorted(_ALLOWED_NUMERIC_STYLES)})")
            if style == "currency" and (not isinstance(rule.get("currency"), str) or len(rule["currency"]) != 3):
                raise ValueError(f"format rule for {property_name!r}: numeric style 'currency' requires a 3-letter 'currency' code")
            if style == "unit" and not isinstance(rule.get("unit"), str):
                raise ValueError(f"format rule for {property_name!r}: numeric style 'unit' requires a 'unit' string (e.g. 'kilogram')")
            notation = rule.get("notation")
            if notation is not None and notation not in _ALLOWED_NUMERIC_NOTATIONS:
                raise ValueError(f"format rule for {property_name!r}: unknown notation {notation!r} (expected one of {sorted(_ALLOWED_NUMERIC_NOTATIONS)})")
            for field in _NUMERIC_INT_FIELDS:
                if field in rule and not isinstance(rule[field], int):
                    raise ValueError(f"format rule for {property_name!r}: {field!r} must be an integer")
            for field in ("prefix", "suffix"):
                if field in rule and not isinstance(rule[field], str):
                    raise ValueError(f"format rule for {property_name!r}: {field!r} must be a string")
            if "useGrouping" in rule and not isinstance(rule["useGrouping"], bool):
                raise ValueError(f"format rule for {property_name!r}: 'useGrouping' must be a boolean")
        elif kind == "datetime":
            style = rule.get("style")
            if style not in _ALLOWED_DATETIME_STYLES:
                raise ValueError(f"format rule for {property_name!r}: unknown datetime style {style!r} (expected one of {sorted(_ALLOWED_DATETIME_STYLES)})")
            if "timezone" in rule and not isinstance(rule["timezone"], str):
                raise ValueError(f"format rule for {property_name!r}: 'timezone' must be an IANA timezone string (e.g. 'America/New_York')")
        elif kind == "resource-link":
            resource_type = rule.get("resourceType")
            if resource_type not in _ALLOWED_RESOURCE_LINK_TYPES:
                raise ValueError(
                    f"format rule for {property_name!r}: unknown resourceType {resource_type!r} "
                    f"(expected one of {sorted(_ALLOWED_RESOURCE_LINK_TYPES)})"
                )
        # "principal" has no extra fields — kind alone is the whole rule.


_ALLOWED_CONDITION_TYPES = {
    "always", "is-null", "string-equals", "string-contains", "string-starts-with", "number-range", "number-equals",
}
_ALLOWED_TEXT_ALIGN = {"left", "center", "right"}
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def _validate_style(property_name: str, index: int, style: object) -> None:
    if not isinstance(style, dict) or not style:
        raise ValueError(f"conditional format rule #{index} for {property_name!r}: 'style' must be a non-empty object")
    if "color" in style and not (isinstance(style["color"], str) and (_HEX_COLOR_RE.match(style["color"]) or style["color"] in _ALLOWED_BADGE_COLORS)):
        raise ValueError(f"conditional format rule #{index} for {property_name!r}: 'color' must be a hex color or one of {sorted(_ALLOWED_BADGE_COLORS)}")
    if "backgroundColor" in style and not (isinstance(style["backgroundColor"], str) and (_HEX_COLOR_RE.match(style["backgroundColor"]) or style["backgroundColor"] in _ALLOWED_BADGE_COLORS)):
        raise ValueError(f"conditional format rule #{index} for {property_name!r}: 'backgroundColor' must be a hex color or one of {sorted(_ALLOWED_BADGE_COLORS)}")
    if "textAlign" in style and style["textAlign"] not in _ALLOWED_TEXT_ALIGN:
        raise ValueError(f"conditional format rule #{index} for {property_name!r}: unknown textAlign {style['textAlign']!r} (expected one of {sorted(_ALLOWED_TEXT_ALIGN)})")


def _validate_conditional_formats(
    *, property_mapping: dict, derived_properties: dict[str, str], conditional_formats: dict[str, list]
) -> None:
    """Same governed tier as `_validate_property_formats` — a genuinely
    separate concern (visual styling of a value already rendered, not the
    value's own textual form), so it's its own field/validator rather than
    a `style` key bolted onto a `PropertyFormatRule`.
    """
    known_properties = set(property_mapping) | set(derived_properties)
    for property_name, rules in conditional_formats.items():
        if property_name not in known_properties:
            raise ValueError(f"conditional format for {property_name!r} names a property this ObjectType doesn't have")
        if not isinstance(rules, list) or not rules:
            raise ValueError(f"conditional format for {property_name!r} must be a non-empty list of rules")
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                raise ValueError(f"conditional format rule #{index} for {property_name!r} must be an object")
            condition = rule.get("condition")
            if not isinstance(condition, dict) or condition.get("type") not in _ALLOWED_CONDITION_TYPES:
                raise ValueError(
                    f"conditional format rule #{index} for {property_name!r}: 'condition.type' must be one of {sorted(_ALLOWED_CONDITION_TYPES)}"
                )
            condition_type = condition["type"]
            if condition_type in ("string-equals", "string-contains", "string-starts-with") and not isinstance(condition.get("value"), str):
                raise ValueError(f"conditional format rule #{index} for {property_name!r}: condition {condition_type!r} requires a string 'value'")
            if condition_type == "number-equals" and not isinstance(condition.get("value"), (int, float)):
                raise ValueError(f"conditional format rule #{index} for {property_name!r}: condition 'number-equals' requires a numeric 'value'")
            if condition_type == "number-range" and "min" not in condition and "max" not in condition:
                raise ValueError(f"conditional format rule #{index} for {property_name!r}: condition 'number-range' requires 'min' and/or 'max'")
            compare_to = rule.get("compareTo")
            if compare_to is not None:
                if not isinstance(compare_to, dict) or compare_to.get("kind") != "property" or compare_to.get("property") not in known_properties:
                    raise ValueError(
                        f"conditional format rule #{index} for {property_name!r}: 'compareTo' must reference a real property on this ObjectType"
                    )
            _validate_style(property_name, index, rule.get("style"))


_ALLOWED_PROPERTY_TYPE_KINDS = {"value_type", "shared_property_type", "struct", "array"}
_ALLOWED_RENDER_HINTS = ALLOWED_RENDER_HINTS


def _validate_struct_field_metadata(property_name: str, rule: dict, *, allow_column: bool = True) -> None:
    """Validate per-field metadata for struct types."""
    if "description" in rule and not isinstance(rule["description"], str):
        raise ValueError(f"property_types entry for {property_name!r}: description must be a string")
    if "main_field" in rule and not isinstance(rule["main_field"], bool):
        raise ValueError(f"property_types entry for {property_name!r}: main_field must be a boolean")
    if "column" in rule:
        if not allow_column:
            raise ValueError(
                f"property_types entry for {property_name!r}: per-field column mapping is only "
                f"allowed on top-level struct fields (not array-of-struct element fields)"
            )
        col = rule["column"]
        if not isinstance(col, str) or not col.strip():
            raise ValueError(f"property_types entry for {property_name!r}: column must be a non-empty string")
        rule["column"] = col.strip()


def _validate_property_control_metadata(
    property_name: str, rule: dict, *, nested: bool, allow_field_column: bool = True
) -> None:
    """Validate top-level property control metadata."""
    if nested and any(
        k in rule
        for k in ("editable", "required", "visibility", "render_hints", "type_classes", "lifecycle_status")
    ):
        raise ValueError(
            f"property_types entry for {property_name!r}: control metadata "
            f"(editable/required/visibility/render_hints/type_classes/lifecycle_status) "
            f"only applies to a top-level property"
        )
    if nested:
        _validate_struct_field_metadata(property_name, rule, allow_column=allow_field_column)
        return
    for flag in ("editable", "required"):
        if flag in rule and not isinstance(rule[flag], bool):
            raise ValueError(f"property_types entry for {property_name!r}: {flag!r} must be a boolean")
    if "visibility" in rule and rule["visibility"] not in ("prominent", "normal", "hidden"):
        raise ValueError(
            f"property_types entry for {property_name!r}: visibility must be "
            f"'prominent', 'normal', or 'hidden'"
        )
    if "lifecycle_status" in rule:
        status = rule["lifecycle_status"]
        from .lifecycle import PROPERTY_LIFECYCLE_STATUSES

        if status not in PROPERTY_LIFECYCLE_STATUSES:
            raise ValueError(
                f"property_types entry for {property_name!r}: lifecycle_status must be "
                f"one of {sorted(PROPERTY_LIFECYCLE_STATUSES)}"
            )
    if "render_hints" in rule:
        try:
            rule["render_hints"] = normalize_render_hints(rule.get("render_hints"))
        except ValueError as exc:
            raise ValueError(f"property_types entry for {property_name!r}: {exc}") from exc
    if "type_classes" in rule:
        try:
            rule["type_classes"] = normalize_type_classes(rule.get("type_classes"))
        except ValueError as exc:
            raise ValueError(f"property_types entry for {property_name!r}: {exc}") from exc


async def _validate_property_types(
    pool: asyncpg.Pool, *, tenant_id: str, property_mapping: dict, derived_properties: dict[str, str], property_types: dict[str, dict]
) -> None:
    """Enforced at publish time, same tier as `_validate_property_formats`
    (a genuinely separate concern — data typing, not display formatting):
    a property_types entry must name a real property, a known `kind`,
    and — for `value_type`/`shared_property_type` — a real, registered
    reference. Nesting is checked structurally, one hop deep, with a
    single named exception: an array's element may be a `struct` (that
    struct's own fields are then leaves-only, matching Foundry's own
    "struct array" shape) — every other nested position (a plain
    struct's own field, or that struct-array-element's own field) stays
    restricted to `value_type`/`shared_property_type`. No storage change
    needed for this — `core.py`'s `_parse_struct_or_array` already
    `json.loads`s any array shape generically.

    A top-level entry may also carry `editable`/`required`/`visibility`
    (property control), plus Foundry-style `render_hints` (list of
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
            # Per-field dataset columns only on top-level structs (Foundry Column mapping).
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


