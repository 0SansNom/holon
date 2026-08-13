"""Render hints validation and prerequisites."""

from __future__ import annotations

from typing import Optional

ALLOWED_RENDER_HINTS = frozenset({
    "searchable",
    "sortable",
    "selectable",
    "identifier",
    "keywords",
    "long_text",
    "low_cardinality",
    "enable_leading_wildcards",
    "enable_regex_queries",
})
_REQUIRES_SEARCHABLE = frozenset({
    "sortable",
    "selectable",
    "low_cardinality",
    "enable_leading_wildcards",
    "enable_regex_queries",
})
_FACET_HINTS = frozenset({"selectable", "low_cardinality"})


def normalize_render_hints(
    render_hints: Optional[list[str]],
    *,
    default: Optional[list[str]] = None,
) -> list[str]:
    """Validate and return a normalized list.

    `default` is used when `render_hints` is None (SPT create uses
    ``["searchable"]``). An explicit empty list means "not searchable".
    """
    if render_hints is None:
        return list(default) if default is not None else []
    if not isinstance(render_hints, list) or not all(isinstance(h, str) for h in render_hints):
        raise ValueError("render_hints must be a list of strings")
    cleaned = [h.strip() for h in render_hints if isinstance(h, str) and h.strip()]
    unknown = set(cleaned) - ALLOWED_RENDER_HINTS
    if unknown:
        raise ValueError(
            f"unknown render_hints {sorted(unknown)} (expected subset of {sorted(ALLOWED_RENDER_HINTS)})"
        )
    # Preserve order, de-dupe.
    seen: set[str] = set()
    ordered: list[str] = []
    for h in cleaned:
        if h not in seen:
            seen.add(h)
            ordered.append(h)
    needs = set(ordered) & _REQUIRES_SEARCHABLE
    if needs and "searchable" not in ordered:
        raise ValueError(
            f"render_hints {sorted(needs)} require 'searchable' "
            f"(Foundry: Searchable must be selected with Sortable/Selectable/Low cardinality)"
        )
    return ordered


def has_render_hint(render_hints: Optional[list], name: str) -> bool:
    return name in (render_hints or [])


def facet_render_hints(property_types: dict | None) -> list[str]:
    """Property API names (and ``struct.field`` paths) with selectable or low_cardinality hints.

    For struct properties, a parent-level facet hint fans out to each declared
    field path so Search can aggregate ``props.address.city``.
    """
    names: list[str] = []
    for prop_name, rule in (property_types or {}).items():
        if not isinstance(rule, dict):
            continue
        hints = rule.get("render_hints") or []
        if not any(h in hints for h in _FACET_HINTS):
            continue
        if rule.get("kind") == "struct":
            fields = rule.get("properties") or {}
            if isinstance(fields, dict) and fields:
                for field_name in fields:
                    names.append(f"{prop_name}.{field_name}")
                continue
        names.append(prop_name)
    return names


def search_capability_hints(property_types: dict | None) -> dict[str, bool]:
    """Whether any property on the type enables advanced search modes."""
    allow_leading = False
    allow_regex = False
    for rule in (property_types or {}).values():
        if not isinstance(rule, dict):
            continue
        hints = rule.get("render_hints") or []
        if "enable_leading_wildcards" in hints:
            allow_leading = True
        if "enable_regex_queries" in hints:
            allow_regex = True
    return {"allow_leading_wildcards": allow_leading, "allow_regex_queries": allow_regex}
