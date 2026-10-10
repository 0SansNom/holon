"""Search document construction and field/marking helpers."""
from __future__ import annotations

from typing import Any

from .search_constants import POLICY_VERSION
from .struct_values import assemble_struct_value


def _is_property_searchable(
    prop_name: str,
    property_types: dict | None,
    shared_property_types: dict | None = None,
) -> bool:
    rule = (property_types or {}).get(prop_name) or {}
    hints = rule.get("render_hints")
    if hints is None and rule.get("kind") == "shared_property_type":
        spt = (shared_property_types or {}).get(rule.get("shared_property_type") or "")
        if isinstance(spt, dict):
            hints = spt.get("render_hints")
    return hints is None or "searchable" in hints

def _searchable_columns(
    property_mapping: dict,
    property_types: dict | None,
    shared_property_types: dict | None = None,
) -> list[str]:
    """Columns included in the unified `text` bag. Default searchable=True
    when render_hints is absent (local or inherited from SPT); omit a column
    only when resolved hints exist and do not include ``searchable``.
    """
    columns: list[str] = []
    for prop_name, column in property_mapping.items():
        if _is_property_searchable(prop_name, property_types, shared_property_types):
            columns.append(column)
    return columns

def _struct_container_from_row(row: dict, column: str, rule: dict) -> dict | None:
    """JSON column + optional per-field ``column`` overlays (field mapping)."""
    assembled = assemble_struct_value(rule, row, column)
    return assembled if isinstance(assembled, dict) else None

def _struct_field_text_fragments(
    row: dict,
    property_mapping: dict,
    property_types: dict | None,
    shared_property_types: dict | None = None,
) -> list[str]:
    """Leaf values from searchable struct properties (search by struct values)."""
    fragments: list[str] = []
    for prop_name, column in property_mapping.items():
        rule = (property_types or {}).get(prop_name) or {}
        if rule.get("kind") != "struct":
            continue
        if not _is_property_searchable(prop_name, property_types, shared_property_types):
            continue
        container = _struct_container_from_row(row, column, rule)
        if not container:
            continue
        for field_name, field_rule in (rule.get("properties") or {}).items():
            if not isinstance(field_rule, dict):
                continue
            value = container.get(field_name)
            if value is None:
                continue
            fragments.append(str(value))
    return fragments

def _ontology_alias_terms(
    property_mapping: dict,
    property_types: dict | None,
    shared_property_types: dict | None = None,
) -> list[str]:
    """Alternate search terms for SPT-backed properties —
    display_name, api_name, and aliases are appended to every indexed row
    so Object Explorer / unified search finds instances by property alias.
    """
    terms: list[str] = []
    seen: set[str] = set()
    for prop_name, rule in (property_types or {}).items():
        if prop_name not in property_mapping:
            continue
        if not isinstance(rule, dict) or rule.get("kind") != "shared_property_type":
            continue
        spt = (shared_property_types or {}).get(rule.get("shared_property_type") or "")
        if not isinstance(spt, dict):
            continue
        candidates = [prop_name, spt.get("api_name"), spt.get("display_name"), *(spt.get("aliases") or [])]
        for raw in candidates:
            if not isinstance(raw, str):
                continue
            term = raw.strip()
            if not term:
                continue
            key = term.casefold()
            if key in seen:
                continue
            seen.add(key)
            terms.append(term)
    return terms

def _keyword_prop_values(row: dict, property_mapping: dict, property_types: dict | None) -> dict[str, Any]:
    """Flatten facetable scalars and struct leaves under ``props``.

    Scalars with sortable/selectable/low_cardinality go to ``props.<apiName>``.
    Struct leaves always go to nested ``props.<struct>.<field>`` so unified
    search can filter with ``prop.address.city=Paris`` (OpenSearch path).
    """
    out: dict[str, Any] = {}
    facet_hints = {"sortable", "selectable", "low_cardinality"}
    for prop_name, column in property_mapping.items():
        rule = (property_types or {}).get(prop_name) or {}
        if rule.get("kind") == "struct":
            container = _struct_container_from_row(row, column, rule)
            if not container:
                continue
            nested: dict[str, str] = {}
            for field_name in (rule.get("properties") or {}):
                value = container.get(field_name)
                if value is None:
                    continue
                nested[field_name] = str(value)
            if nested:
                out[prop_name] = nested
            continue
        hints = rule.get("render_hints") or []
        if not facet_hints.intersection(hints):
            continue
        value = row.get(column)
        if value is None:
            continue
        out[prop_name] = str(value)
    return out


_sortable_prop_values = _keyword_prop_values


def _is_confidential_property(prop_name: str, column: str, classifications: dict[str, str]) -> bool:
    """Classifications are keyed by source column (catalog) or API name."""
    return classifications.get(column) == "confidential" or classifications.get(prop_name) == "confidential"

def confidential_property_names(
    property_mapping: dict,
    classifications: dict[str, str] | None,
    *,
    object_classification: str | None = None,
) -> set[str]:
    """Ontology property names whose stored value is confidential.

    A property with no classification row inherits a confidential object
    rollup. An explicit public or internal classification stays public.
    """
    classified = classifications or {}
    names = {
        prop
        for prop, column in property_mapping.items()
        if _is_confidential_property(prop, column, classified)
    }
    if object_classification == "confidential":
        for prop, column in property_mapping.items():
            if prop not in classified and column not in classified:
                names.add(prop)
    return names

def _struct_fragments_for_property(
    row: dict,
    prop_name: str,
    column: str,
    property_types: dict | None,
    shared_property_types: dict | None,
) -> list[str]:
    rule = (property_types or {}).get(prop_name) or {}
    if rule.get("kind") != "struct":
        return []
    if not _is_property_searchable(prop_name, property_types, shared_property_types):
        return []
    container = _struct_container_from_row(row, column, rule)
    if not container:
        return []
    fragments: list[str] = []
    for field_name, field_rule in (rule.get("properties") or {}).items():
        if not isinstance(field_rule, dict):
            continue
        value = container.get(field_name)
        if value is None:
            continue
        fragments.append(str(value))
    return fragments

def build_search_document(
    *,
    object_type_name: str,
    tenant_id: str,
    classification: str,
    property_mapping: dict,
    row: dict,
    property_types: dict | None = None,
    shared_property_types: dict | None = None,
    property_classifications: dict[str, str] | None = None,
    instance_markings: list[str] | None = None,
) -> dict[str, Any]:
    """One OpenSearch document. Confidential values never enter ``text`` or ``props``."""
    classifications = property_classifications or {}
    secret_names = confidential_property_names(
        property_mapping, classifications, object_classification=classification
    )
    public_parts: list[str] = []
    secret_parts: list[str] = []
    for prop_name, column in property_mapping.items():
        if _is_property_searchable(prop_name, property_types, shared_property_types):
            raw = row.get(column, "")
            if raw not in (None, ""):
                bucket = secret_parts if prop_name in secret_names else public_parts
                bucket.append(str(raw))
        fragments = _struct_fragments_for_property(
            row, prop_name, column, property_types, shared_property_types
        )
        if prop_name in secret_names:
            secret_parts.extend(fragments)
        else:
            public_parts.extend(fragments)

    alias_terms = _ontology_alias_terms(property_mapping, property_types, shared_property_types)
    alias_suffix = (" " + " ".join(alias_terms)) if alias_terms else ""
    text = " ".join(part for part in public_parts if part) + alias_suffix
    confidential_text = " ".join(part for part in secret_parts if part)

    instance_id = row["id"]
    document: dict[str, Any] = {
        "urn": f"{object_type_name}:{tenant_id}:{instance_id}",
        "object_type": object_type_name,
        "tenant_id": tenant_id,
        "classification": classification,
        "policy_version": POLICY_VERSION,
        "text": text,
    }
    if confidential_text:
        document["confidential_text"] = confidential_text
    required = _required_marking_tokens(instance_markings)
    if required:
        document["required_markings"] = required
        document["required_marking_count"] = len(required)
    props = _keyword_prop_values(row, property_mapping, property_types)
    public_props = {key: value for key, value in props.items() if key not in secret_names}
    secret_props = {key: value for key, value in props.items() if key in secret_names}
    if public_props:
        document["props"] = public_props
    if secret_props:
        document["confidential_props"] = secret_props
    return document

def _required_marking_tokens(markings: list[str] | None) -> list[str]:
    return [f"mark:{name}" for name in (markings or []) if name]

def _principal_marking_tokens(held_markings: list[str] | None) -> list[str]:
    tokens = _required_marking_tokens(held_markings)
    return tokens or ["mark:__none__"]

def _rebac_object_type_filter(allowed_object_types: list[str]) -> dict[str, Any]:
    return {"terms": {"object_type": list(allowed_object_types)}}

def _marking_filter(held_marking_tokens: list[str]) -> dict[str, Any]:
    """Unmarked documents stay visible. Marked documents require the
    principal to hold every `required_markings` token (terms_set).
    """
    return {
        "bool": {
            "should": [
                {"bool": {"must_not": {"exists": {"field": "required_markings"}}}},
                {
                    "terms_set": {
                        "required_markings": {
                            "terms": held_marking_tokens,
                            "minimum_should_match_field": "required_marking_count",
                        }
                    }
                },
            ],
            "minimum_should_match": 1,
        }
    }

