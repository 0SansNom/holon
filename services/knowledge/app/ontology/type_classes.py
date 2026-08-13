"""Type classes metadata tags."""

from __future__ import annotations

import re
from typing import Optional

_TYPE_CLASS_RE = re.compile(
    r"^(?:"
    r"[a-z][a-z0-9_-]{0,63}"  # bare tag
    r"|"
    r"[a-z][a-z0-9_-]{0,63}:[A-Za-z0-9_.:-]{1,128}"  # kind:name
    r")$"
)

# Catalog of type classes Holon understands (storage accepts any valid string;
# this list is documentation + UI suggestions + consumer keys).
KNOWN_TYPE_CLASSES: dict[str, dict[str, str]] = {
    "hubble:media_url": {
        "applies_to": "property",
        "description": "Render property value as media in Object View",
    },
    "hubble:icon": {
        "applies_to": "property",
        "description": "URL property used as the object icon",
    },
    "hierarchy:parent": {
        "applies_to": "relation",
        "description": "Link direction is parent in a hierarchy (Object View breadcrumbs)",
    },
    "hubble-oe:hide-action": {
        "applies_to": "action",
        "description": "Hide Action from Object Explorer / Object View Actions dropdown",
    },
    "actions:generate_uuid": {
        "applies_to": "action",
        "description": "Prefill a string parameter with a new UUID at invoke time",
    },
    "actions:prefill_current_user": {
        "applies_to": "action",
        "description": "Prefill a string parameter with the current principal URN",
    },
    "actions:view_object_with_type": {
        "applies_to": "action",
        "description": "Success toast should highlight the affected object",
    },
}


def normalize_type_class(raw: str) -> str:
    cleaned = (raw or "").strip()
    if not cleaned:
        raise ValueError("type class must be a non-empty string")
    if not _TYPE_CLASS_RE.match(cleaned):
        raise ValueError(
            f"invalid type class {cleaned!r} — expected a bare tag (e.g. 'priority') "
            f"or Foundry 'kind:name' (e.g. 'hubble:media_url', 'hierarchy:parent')"
        )
    return cleaned


def normalize_type_classes(type_classes: Optional[list[str]]) -> list[str]:
    if type_classes is None:
        return []
    if not isinstance(type_classes, list) or not all(isinstance(c, str) for c in type_classes):
        raise ValueError("type_classes must be a list of strings")
    return [normalize_type_class(c) for c in type_classes]


def parse_type_class(raw: str) -> tuple[str, str]:
    """Return `(kind, name)`. Bare tags become `('custom', tag)`."""
    cleaned = normalize_type_class(raw)
    if ":" in cleaned:
        kind, name = cleaned.split(":", 1)
        return kind, name
    return "custom", cleaned


def has_type_class(type_classes: Optional[list], kind: str, name: str) -> bool:
    target = f"{kind}:{name}"
    for item in type_classes or []:
        if not isinstance(item, str):
            continue
        try:
            if normalize_type_class(item) == target:
                return True
        except ValueError:
            continue
    return False


def find_property_with_type_class(
    property_types: Optional[dict], kind: str, name: str
) -> Optional[str]:
    """Return the first property name carrying `kind:name`, else None."""
    if not isinstance(property_types, dict):
        return None
    for prop_name, rule in property_types.items():
        if not isinstance(rule, dict):
            continue
        if has_type_class(rule.get("type_classes"), kind, name):
            return prop_name
    return None
