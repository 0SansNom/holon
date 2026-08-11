"""ObjectType lifecycle: propose a draft, publish it. The widest-reaching
module in this package — publishing touches interfaces, derived-property
Function registration, project scope, and markings validation all in one
transaction — kept intact rather than fragmented further, per the plan's
own note on this function.

Two internal helpers separate validation from writing so that `branching.
review_branch` can include the branch-merge `UPDATE` in the same atomic
transaction as the publish writes (`_write_publish`), without duplicating
the validation logic (`_run_publish_validations`).
"""

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
from .interfaces import get_interface_type


def _action_local_name(action_name: str) -> str:
    """Interface `required_actions` store the short local name
    (`putOnCreditHold`); registry keys are usually `Type.action`.
    Bare names (no dot) pass through unchanged.
    """
    return action_name.split(".", 1)[1] if "." in action_name else action_name


async def _actions_available_on_object_type(
    pool: asyncpg.Pool, *, tenant_id: str, object_type_name: str, implements: list[str]
) -> set[str]:
    """Short action names invokable on this ObjectType after publish —
    hardcoded `ACTION_DEFINITIONS`, declarative ActionTypes targeting the
    OT directly, and ActionTypes targeting any interface this version
    declares in `implements` (Actions-on-interfaces).
    """
    from ..actions import ACTION_DEFINITIONS
    from .action_types import list_action_types

    implements_set = set(implements)
    names: set[str] = set()
    for action_name, definition in ACTION_DEFINITIONS.items():
        if definition.get("target_object_type") == object_type_name:
            names.add(_action_local_name(action_name))
        target_interface = definition.get("target_interface")
        if target_interface and target_interface in implements_set:
            names.add(_action_local_name(action_name))

    for action_type in await list_action_types(pool, tenant_id):
        if action_type.get("target_object_type") == object_type_name:
            names.add(_action_local_name(action_type["name"]))
        target_interface = action_type.get("target_interface")
        if target_interface and target_interface in implements_set:
            names.add(_action_local_name(action_type["name"]))
    return names


async def _validate_implements(
    pool: asyncpg.Pool, *, tenant_id: str, object_type_name: str, property_mapping: dict, implements: list[str]
) -> None:
    """Enforced at publish time (same synchronous-validation treatment
    `create_relation_type` already gives cardinality/endpoints):
    declaring conformance to an interface is a checked promise, not a
    label. Every required property must exist on this version's own
    `property_mapping`; every required action must be a real, registered
    Action available on this ObjectType (hardcoded or declarative,
    OT-targeted or interface-targeted for an interface this draft
    implements).
    """
    object_action_names = await _actions_available_on_object_type(
        pool, tenant_id=tenant_id, object_type_name=object_type_name, implements=implements
    )

    for interface_name in implements:
        interface = await get_interface_type(pool, tenant_id, interface_name)
        if interface is None:
            raise ValueError(f"unknown interface: {interface_name!r}")
        missing_properties = [p for p in interface["required_properties"] if p not in property_mapping]
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
        if source_name == object_type_name and local_name == link_name:
            return relation
        if target_name == object_type_name and relation.get("target_property") == link_name:
            return relation
    return None


def _link_aggregate_path(rule: dict) -> list[str]:
    """Foundry-style multi-hop path (1–3). `path` wins; legacy single
    `relation` is treated as a one-hop path.
    """
    path = rule.get("path")
    if path is not None:
        return path if isinstance(path, list) else []
    relation = rule.get("relation")
    return [relation] if isinstance(relation, str) and relation else []


async def _validate_derived_properties(
    pool: asyncpg.Pool, *, derived_properties: dict[str, object], object_type_name: str, tenant_id: str,
    property_types: Optional[dict[str, dict]] = None,
) -> None:
    """Enforced at publish time, same tier as `_validate_implements`. A
    plain string must name a real, currently-*active* Function plugin —
    the original shape, unchanged. A `{"kind": "link_aggregate", ...}`
    dict is a Foundry-style reducer over a RelationType path (`path` of
    1–3 link names, or legacy single `relation`): each hop must resolve
    from the type reached so far, `aggregate` must be a known aggregate,
    and — unless `aggregate` is `count`, which needs no value to read —
    `property` must be a real mapped property on the *final* related
    ObjectType. A `{"kind": "struct_reducer", ...}` dict is a
    Foundry-style reducer over one of *this* ObjectType's own array
    properties: `property` must name an `array`-kind `property_types`
    entry, `reducer` must be a known reducer, and — for the field-based
    reducers (`latest`/`earliest`/`max`/`min`) — `by` is required and
    must be a real field when the array's element is a `struct`, or must
    be absent when the element is a scalar (nothing to key by; reduce the
    values directly). Import kept local to avoid a module-load-order
    assumption between this package and `function_registry.py` (neither
    currently imports the other at top level; this keeps it that way).
    """
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
                f"derived property {property_name!r}: link_aggregate requires 'path' (1–"
                f"{_MAX_LINK_AGGREGATE_HOPS} link names) or a single 'relation'"
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
# The same small, closed vocabulary Blueprint's own `Intent` type uses
# (`Tag`/`Callout` `intent` prop) — a `badge` rule's colors map values
# directly onto it so the frontend never needs its own color table.
_ALLOWED_BADGE_COLORS = {"primary", "success", "warning", "danger", "none"}

# `numeric` rule fields map directly onto JS `Intl.NumberFormat` options
# (see PropertyFormat.tsx) — kept name-for-name so the frontend can pass
# the rule straight through with no translation layer. `style` covers
# Foundry's currency/unit/percent/decimal categories in one kind, rather
# than four separate ones — the only thing that actually changes between
# them is this one option.
_ALLOWED_NUMERIC_STYLES = {"decimal", "currency", "percent", "unit"}
_ALLOWED_NUMERIC_NOTATIONS = {"standard", "compact", "scientific", "engineering"}
_NUMERIC_INT_FIELDS = (
    "minimumFractionDigits", "maximumFractionDigits",
    "minimumSignificantDigits", "maximumSignificantDigits", "minimumIntegerDigits",
)

# Foundry's 6 documented date/time formats: date-only, long/short
# datetime, ISO 8601, relative ("8 minutes ago"), time-only.
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
_ALLOWED_RENDER_HINTS = frozenset({"searchable", "sortable", "selectable", "identifier"})
_ALLOWED_TYPE_CLASS_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def _validate_property_control_metadata(property_name: str, rule: dict, *, nested: bool) -> None:
    """Top-level-only Foundry-style property control metadata."""
    if nested and any(k in rule for k in ("editable", "required", "visibility", "render_hints", "type_classes")):
        raise ValueError(
            f"property_types entry for {property_name!r}: control metadata "
            f"(editable/required/visibility/render_hints/type_classes) only applies to a top-level property"
        )
    if nested:
        return
    for flag in ("editable", "required"):
        if flag in rule and not isinstance(rule[flag], bool):
            raise ValueError(f"property_types entry for {property_name!r}: {flag!r} must be a boolean")
    if "visibility" in rule and rule["visibility"] not in ("prominent", "normal", "hidden"):
        raise ValueError(
            f"property_types entry for {property_name!r}: visibility must be "
            f"'prominent', 'normal', or 'hidden'"
        )
    if "render_hints" in rule:
        hints = rule["render_hints"]
        if not isinstance(hints, list) or not all(isinstance(h, str) for h in hints):
            raise ValueError(f"property_types entry for {property_name!r}: render_hints must be a list of strings")
        unknown = set(hints) - _ALLOWED_RENDER_HINTS
        if unknown:
            raise ValueError(
                f"property_types entry for {property_name!r}: unknown render_hints {sorted(unknown)} "
                f"(expected subset of {sorted(_ALLOWED_RENDER_HINTS)})"
            )
    if "type_classes" in rule:
        classes = rule["type_classes"]
        if not isinstance(classes, list) or not all(isinstance(c, str) for c in classes):
            raise ValueError(f"property_types entry for {property_name!r}: type_classes must be a list of strings")
        for cls in classes:
            if not _ALLOWED_TYPE_CLASS_RE.match(cls):
                raise ValueError(
                    f"property_types entry for {property_name!r}: invalid type class {cls!r} "
                    f"(expected lowercase identifier, e.g. 'priority')"
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

    async def _validate_leaf(property_name: str, rule: dict, *, in_struct: bool = False, in_array: bool = False) -> None:
        nested = in_struct or in_array
        kind = rule.get("kind")
        _validate_property_control_metadata(property_name, rule, nested=nested)
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
            if await shared_property_types_module.get_shared_property_type(pool, tenant_id, shared_property_type_name) is None:
                raise ValueError(f"property_types entry for {property_name!r} names unknown shared_property_type {shared_property_type_name!r}")
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
            for nested_name, nested_rule in nested_properties.items():
                await _validate_leaf(f"{property_name}.{nested_name}", nested_rule, in_struct=True, in_array=in_array)
            return
        if kind == "array":
            if nested:
                raise ValueError(f"property_types entry for {property_name!r}: struct/array nesting is limited to one level")
            element_rule = rule.get("element")
            if not isinstance(element_rule, dict):
                raise ValueError(f"property_types entry for {property_name!r}: 'array' requires an 'element' type rule")
            await _validate_leaf(f"{property_name}[]", element_rule, in_array=True)

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


async def propose_object_type_version(
    pool: asyncpg.Pool,
    *,
    object_type_urn: str,
    property_mapping: Optional[dict] = None,
    description: Optional[str] = None,
    implements: Optional[list[str]] = None,
    derived_properties: Optional[dict[str, str]] = None,
    project_urn: Optional[str] = None,
    markings: Optional[list[str]] = None,
    property_formats: Optional[dict[str, dict]] = None,
    conditional_formats: Optional[dict[str, list]] = None,
    property_types: Optional[dict[str, dict]] = None,
    primary_key: Optional[str] = None,
    title_key: Optional[str] = None,
    plural_display_name: Optional[str] = None,
    lifecycle_status: Optional[str] = None,
    visibility: Optional[str] = None,
    icon: Optional[str] = None,
) -> dict:
    """Creates a `draft` version — never touches the live `object_type`
    row (everything else in this build keeps reading the current
    *published* state until `publish_object_type_version` says
    otherwise). A partial update (only `description`, say) carries the
    current published value forward for whatever isn't overridden, so
    proposing a version never silently blanks out the other field.
    """
    current = await get_object_type(pool, object_type_urn)
    if current is None:
        raise ValueError(f"unknown ObjectType: {object_type_urn}")

    # Drafts that fail publishing validation remain in `object_type_version`
    # as unpublished drafts. Compute the next version number relative to the
    # maximum version in `object_type_version` to prevent UNIQUE constraint collisions.
    highest_known_version = await pool.fetchval(
        "SELECT COALESCE(MAX(version), 0) FROM object_type_version WHERE object_type_urn = $1", object_type_urn
    )
    next_version = max(current["version"], highest_known_version) + 1
    new_mapping = property_mapping if property_mapping is not None else current["property_mapping"]
    if isinstance(new_mapping, str):
        new_mapping = json.loads(new_mapping)
    new_description = description if description is not None else current["description"]
    new_implements = implements if implements is not None else (current.get("implements") or [])
    new_derived_properties = (
        derived_properties if derived_properties is not None else (current.get("derived_properties") or {})
    )
    new_project_urn = project_urn if project_urn is not None else current.get("project_urn")
    new_markings = markings if markings is not None else (current.get("markings") or [])
    new_property_formats = (
        property_formats if property_formats is not None else (current.get("property_formats") or {})
    )
    new_conditional_formats = (
        conditional_formats if conditional_formats is not None else (current.get("conditional_formats") or {})
    )
    new_property_types = (
        property_types if property_types is not None else (current.get("property_types") or {})
    )
    new_primary_key = primary_key if primary_key is not None else (current.get("primary_key") or "id")
    new_title_key = title_key if title_key is not None else current.get("title_key")
    new_plural = plural_display_name if plural_display_name is not None else (current.get("plural_display_name") or "")
    new_lifecycle = lifecycle_status if lifecycle_status is not None else (current.get("lifecycle_status") or "experimental")
    new_visibility = visibility if visibility is not None else (current.get("visibility") or "normal")
    new_icon = icon if icon is not None else current.get("icon")

    validate_ot_metadata(
        property_mapping=new_mapping,
        primary_key=new_primary_key,
        title_key=new_title_key,
        lifecycle_status=new_lifecycle,
        visibility=new_visibility,
    )

    await pool.execute(
        """
        INSERT INTO object_type_version
            (object_type_urn, tenant_id, version, property_mapping, description, implements, derived_properties,
             project_urn, markings, property_formats, conditional_formats, property_types,
             primary_key, title_key, plural_display_name, lifecycle_status, visibility, icon, status)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6::jsonb, $7::jsonb, $8, $9::jsonb, $10::jsonb, $11::jsonb, $12::jsonb,
                $13, $14, $15, $16, $17, $18, 'draft')
        """,
        object_type_urn, current["tenant_id"], next_version,
        json.dumps(new_mapping), new_description, json.dumps(new_implements), json.dumps(new_derived_properties),
        new_project_urn, json.dumps(new_markings), json.dumps(new_property_formats),
        json.dumps(new_conditional_formats), json.dumps(new_property_types),
        new_primary_key, new_title_key, new_plural, new_lifecycle, new_visibility, new_icon,
    )
    return await get_object_type_version(pool, object_type_urn, next_version)


async def _run_publish_validations(
    pool: asyncpg.Pool,
    *,
    draft: dict,
    current: dict | None,
    object_type_name: str,
    implements: list,
    derived_properties: dict,
    project_urn: Optional[str],
    markings: list,
    property_formats: dict,
    conditional_formats: dict,
    property_types: dict,
    identity_url: Optional[str] = None,
    identity_token: Optional[str] = None,
) -> None:
    """All publish-time validations, extracted so they run *outside* a
    database transaction. Some validations make HTTP calls (`_validate_project_scope`
    via httpx) — holding a DB connection open for the duration of a remote
    request would waste a pool slot unnecessarily. The two sync validators
    (`_validate_property_formats`, `_validate_conditional_formats`) are
    also called here without `await`.
    """
    primary_key = draft.get("primary_key") or "id"
    title_key = draft.get("title_key")
    lifecycle_status = draft.get("lifecycle_status") or "experimental"
    visibility = draft.get("visibility") or "normal"
    validate_ot_metadata(
        property_mapping=draft["property_mapping"],
        primary_key=primary_key,
        title_key=title_key,
        lifecycle_status=lifecycle_status,
        visibility=visibility,
    )
    if current and (current.get("lifecycle_status") or "experimental") == "active":
        live_pk = current.get("primary_key") or "id"
        if primary_key != live_pk:
            raise ValueError(
                f"cannot change primary_key from {live_pk!r} to {primary_key!r} while "
                f"lifecycle_status is active — deprecate or keep the existing key"
            )
    if implements:
        await _validate_implements(
            pool,
            tenant_id=draft["tenant_id"],
            object_type_name=object_type_name,
            property_mapping=draft["property_mapping"],
            implements=implements,
        )
    if derived_properties:
        await _validate_derived_properties(
            pool,
            derived_properties=derived_properties,
            object_type_name=object_type_name,
            tenant_id=draft["tenant_id"],
            property_types=property_types,
        )
    if project_urn:
        if identity_url is None or identity_token is None:
            raise ValueError("project_urn is set but no identity_url/identity_token was provided to validate it against")
        await _validate_project_scope(identity_url=identity_url, project_urn=project_urn, identity_token=identity_token)
    if markings:
        await markings_module._validate_markings(pool, tenant_id=draft["tenant_id"], markings=markings)
    if property_formats:
        _validate_property_formats(
            property_mapping=draft["property_mapping"],
            derived_properties=derived_properties,
            property_formats=property_formats,
        )
    if conditional_formats:
        _validate_conditional_formats(
            property_mapping=draft["property_mapping"],
            derived_properties=derived_properties,
            conditional_formats=conditional_formats,
        )
    if property_types:
        await _validate_property_types(
            pool,
            tenant_id=draft["tenant_id"],
            property_mapping=draft["property_mapping"],
            derived_properties=derived_properties,
            property_types=property_types,
        )


async def _write_publish(
    conn: asyncpg.Connection,
    *,
    object_type_urn: str,
    version: int,
    draft: dict,
    current: dict | None,
    previous_version: Optional[int],
    implements: list,
    derived_properties: dict,
    project_urn: Optional[str],
    markings: list,
    property_formats: dict,
    conditional_formats: dict,
    property_types: dict,
) -> None:
    """Execute the publish writes inside an already-open transaction.

    Separated from validation so that callers can include additional writes
    (e.g. a branch-merge status update) in the same atomic transaction
    without duplicating the publish logic.

    Acquires a row-level lock (`FOR UPDATE`) on the `object_type` row first
    to serialise concurrent publishments on the same ObjectType — a second
    concurrent call will block here until this transaction commits or rolls
    back, preventing two concurrent callers from each validating against the
    same version and then both writing. Re-checks monotonicity under that
    lock so a stale draft (version ≤ live) cannot silently regress the
    published definition even if another publish raced ahead between the
    caller's pre-checks and this write.
    """
    locked = await conn.fetchrow("SELECT version FROM object_type WHERE urn = $1 FOR UPDATE", object_type_urn)
    live_version = locked["version"] if locked is not None else None
    if live_version is not None and version <= live_version:
        raise ValueError(
            f"cannot publish version {version} of {object_type_urn}: "
            f"live is already at version {live_version}"
        )
    # Prefer the locked read for the event payload — `previous_version`
    # passed by the caller may be stale after a concurrent publish.
    event_previous_version = live_version if live_version is not None else previous_version
    await conn.execute(
        "UPDATE object_type_version SET status = 'published', published_at = now() WHERE object_type_urn = $1 AND version = $2",
        object_type_urn, version,
    )
    await conn.execute(
        """
        UPDATE object_type SET version = $1, property_mapping = $2::jsonb, description = $3,
            implements = $4::jsonb, derived_properties = $5::jsonb, project_urn = $6, markings = $7::jsonb,
            property_formats = $8::jsonb, conditional_formats = $9::jsonb, property_types = $10::jsonb,
            primary_key = $11, title_key = $12, plural_display_name = $13,
            lifecycle_status = $14, visibility = $15, icon = $16
        WHERE urn = $17
        """,
        version, json.dumps(draft["property_mapping"]), draft["description"],
        json.dumps(implements), json.dumps(derived_properties), project_urn, json.dumps(markings),
        json.dumps(property_formats), json.dumps(conditional_formats), json.dumps(property_types),
        draft.get("primary_key") or "id", draft.get("title_key"), draft.get("plural_display_name") or "",
        draft.get("lifecycle_status") or "experimental", draft.get("visibility") or "normal", draft.get("icon"),
        object_type_urn,
    )
    event_id = uuid.uuid4().hex
    event = EventEnvelope(
        event_id=event_id,
        event_type="knowledge.objecttype.published",
        tenant_id=draft["tenant_id"],
        aggregate_type="ObjectType",
        aggregate_id=object_type_urn,
        correlation_id=event_id,
        partition_key=f"{draft['tenant_id']}/{object_type_urn}",
        producer="knowledge-platform@0.1.0",
        actor=EventActor(type="service_account", urn=build_urn(draft["tenant_id"], "global", "service-account", "ontology-governance")),
        payload={
            "object_type_urn": object_type_urn,
            "name": current["name"] if current else object_type_urn,
            "version": version,
            "previous_version": event_previous_version,
        },
    )
    await outbox.enqueue(conn, event)


async def publish_object_type_version(
    pool: asyncpg.Pool,
    *,
    object_type_urn: str,
    version: int,
    identity_url: Optional[str] = None,
    identity_token: Optional[str] = None,
) -> dict:
    """The only thing that ever updates the live `object_type` row past
    its bootstrap state — every other reader in this build
    (`resolver.py`, `serving_store.py`, `search.py`, every `/objects/...`
    endpoint) keeps working unchanged, since they all read `object_type`
    as before. Publishes `knowledge.objecttype.published` (transactional outbox).

    `identity_url`/`identity_token` are only required when the draft
    actually declares a `project_urn` — kept optional rather than forcing
    every caller to thread through a dependency it doesn't need.
    """
    draft = await get_object_type_version(pool, object_type_urn, version)
    if draft is None:
        raise ValueError(f"no version {version} found for {object_type_urn}")
    if draft["status"] == "published":
        raise ValueError(f"version {version} of {object_type_urn} is already published")

    current = await get_object_type(pool, object_type_urn)
    previous_version = current["version"] if current else None
    # Fast-fail before expensive validations (and again under FOR UPDATE
    # in `_write_publish`) so publishing an older draft cannot silently
    # regress the live definition.
    if previous_version is not None and version <= previous_version:
        raise ValueError(
            f"cannot publish version {version} of {object_type_urn}: "
            f"live is already at version {previous_version}"
        )
    object_type_name = current["name"] if current else object_type_urn.rsplit(":", 1)[-1]
    implements = draft.get("implements") or []
    derived_properties = draft.get("derived_properties") or {}
    project_urn = draft.get("project_urn")
    markings = draft.get("markings") or []
    property_formats = draft.get("property_formats") or {}
    conditional_formats = draft.get("conditional_formats") or {}
    property_types = draft.get("property_types") or {}

    await _run_publish_validations(
        pool,
        draft=draft,
        current=current,
        object_type_name=object_type_name,
        implements=implements,
        derived_properties=derived_properties,
        project_urn=project_urn,
        markings=markings,
        property_formats=property_formats,
        conditional_formats=conditional_formats,
        property_types=property_types,
        identity_url=identity_url,
        identity_token=identity_token,
    )

    async with pool.acquire() as conn, conn.transaction():
        await _write_publish(
            conn,
            object_type_urn=object_type_urn,
            version=version,
            draft=draft,
            current=current,
            previous_version=previous_version,
            implements=implements,
            derived_properties=derived_properties,
            project_urn=project_urn,
            markings=markings,
            property_formats=property_formats,
            conditional_formats=conditional_formats,
            property_types=property_types,
        )

    return await get_object_type(pool, object_type_urn)
