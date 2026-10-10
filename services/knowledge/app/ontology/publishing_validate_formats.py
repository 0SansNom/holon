"""Property format / style / conditional-format publish checks."""
from __future__ import annotations

import re

from .render_hints import ALLOWED_RENDER_HINTS, normalize_render_hints
from .type_classes import normalize_type_classes


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

