"""Unified Search — OpenSearch integration."""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from holon_common import Principal

from .struct_values import assemble_struct_value

INDEX_NAME = "holon-search"

_INDEX_MAPPING = {
    "mappings": {
        "dynamic_templates": [
            {
                "props_as_keyword": {
                    "path_match": "props.*",
                    "mapping": {"type": "keyword"},
                }
            }
        ],
        "properties": {
            "urn": {"type": "keyword"},
            "object_type": {"type": "keyword"},
            "tenant_id": {"type": "keyword"},
            "classification": {"type": "keyword"},
            "entitlement_tokens": {"type": "keyword"},
            "required_markings": {"type": "keyword"},
            "required_marking_count": {"type": "integer"},
            "text": {"type": "text"},
            "props": {"type": "object", "dynamic": True},
        },
    }
}


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
    """JSON column + optional per-field ``column`` overlays (Foundry field mapping)."""
    assembled = assemble_struct_value(rule, row, column)
    return assembled if isinstance(assembled, dict) else None


def _struct_field_text_fragments(
    row: dict,
    property_mapping: dict,
    property_types: dict | None,
    shared_property_types: dict | None = None,
) -> list[str]:
    """Leaf values from searchable struct properties (Foundry: search by struct values)."""
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
    """Foundry-style alternate search terms for SPT-backed properties —
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


def selectable_property_names(property_types: dict | None) -> list[str]:
    """API names (and ``struct.field`` paths) used for Search property facets."""
    from .ontology.render_hints import facet_render_hints

    return facet_render_hints(property_types)


def _text_query_clause(
    query_text: str,
    *,
    allow_leading_wildcards: bool = False,
    allow_regex: bool = False,
) -> dict[str, Any]:
    """Build the primary text clause for unified search."""
    stripped = query_text.strip()
    if allow_regex and stripped.startswith("/") and stripped.endswith("/") and len(stripped) > 2:
        pattern = stripped[1:-1]
        return {"regexp": {"text": {"value": pattern, "flags": "ALL"}}}
    if allow_leading_wildcards:
        return {
            "query_string": {
                "query": query_text,
                "fields": ["text"],
                "allow_leading_wildcard": True,
                "analyze_wildcard": True,
            }
        }
    return {"simple_query_string": {"query": query_text, "fields": ["text"]}}


def _build_post_filter(
    *,
    object_type: Optional[str] = None,
    object_types: Optional[list[str]] = None,
    property_filters: Optional[dict[str, str]] = None,
) -> Optional[dict[str, Any]]:
    clauses: list[dict[str, Any]] = []
    if object_type:
        clauses.append({"term": {"object_type": object_type}})
    elif object_types is not None:
        clauses.append({"terms": {"object_type": list(object_types)}})
    for prop, value in (property_filters or {}).items():
        if not prop or value is None:
            continue
        # `props.*` is mapped directly to `keyword` — no `.keyword`
        # sub-field to fall back to, same as the aggregations above.
        clauses.append({"term": {f"props.{prop}": value}})
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"bool": {"filter": clauses}}


def _entitlement_tokens_for(classification: str, allowed_countries: set[str]) -> list[str]:
    """ABAC half of R8.6 — country clearance. ReBAC is applied at query
    time as an `object_type` filter (see `readable_object_type_names`),
    not as a second token on the document: grants change without a
    reindex, and SpiceDB object IDs are not reversible into URNs.
    """
    if classification != "confidential":
        return ["public-read"]
    return [f"country:{country}" for country in sorted(allowed_countries)]


def _principal_tokens(principal: Principal) -> list[str]:
    tokens = ["public-read"]
    if principal.country:
        tokens.append(f"country:{principal.country}")
    return tokens


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


async def delete_object_type_documents(
    base_url: str,
    password: str,
    *,
    object_type_name: str,
    tenant_id: str,
) -> None:
    """Remove all indexed documents for one ObjectType (before reindex)."""
    body = {
        "query": {
            "bool": {
                "filter": [
                    {"term": {"object_type": object_type_name}},
                    {"term": {"tenant_id": tenant_id}},
                ]
            }
        },
        "conflicts": "proceed",
    }
    async with httpx.AsyncClient(auth=("admin", password), timeout=30.0) as client:
        response = await client.post(f"{base_url}/{INDEX_NAME}/_delete_by_query", json=body)
        if response.status_code not in (200, 404):
            response.raise_for_status()
        await client.post(f"{base_url}/{INDEX_NAME}/_refresh", json={})


_MAPPING_ADDITIONS = {
    "properties": {
        "required_markings": {"type": "keyword"},
        "required_marking_count": {"type": "integer"},
    }
}


async def ensure_index(base_url: str, password: str) -> None:
    async with httpx.AsyncClient(auth=("admin", password), timeout=10.0) as client:
        response = await client.put(f"{base_url}/{INDEX_NAME}", json=_INDEX_MAPPING)
        if response.status_code not in (200, 400):  # 400 covers "already exists"
            response.raise_for_status()
        mapping_response = await client.put(f"{base_url}/{INDEX_NAME}/_mapping", json=_MAPPING_ADDITIONS)
        if mapping_response.status_code not in (200, 400):
            mapping_response.raise_for_status()


async def index_rows(
    base_url: str,
    password: str,
    *,
    object_type_name: str,
    tenant_id: str,
    classification: str,
    property_mapping: dict,
    rows: list[dict],
    allowed_countries: set[str],
    property_types: dict | None = None,
    shared_property_types: dict | None = None,
    instance_markings: dict[str, list[str]] | None = None,
) -> None:
    if not rows:
        return
    tokens = _entitlement_tokens_for(classification, allowed_countries)
    text_columns = _searchable_columns(property_mapping, property_types, shared_property_types)
    alias_terms = _ontology_alias_terms(property_mapping, property_types, shared_property_types)
    alias_suffix = (" " + " ".join(alias_terms)) if alias_terms else ""
    lines: list[str] = []
    for row in rows:
        instance_id = row["id"]
        doc_id = f"{object_type_name}:{tenant_id}:{instance_id}"
        text_parts = [str(row.get(column, "")) for column in text_columns]
        text_parts.extend(
            _struct_field_text_fragments(row, property_mapping, property_types, shared_property_types)
        )
        text = " ".join(part for part in text_parts if part) + alias_suffix
        document: dict[str, Any] = {
            "urn": doc_id,
            "object_type": object_type_name,
            "tenant_id": tenant_id,
            "classification": classification,
            "entitlement_tokens": tokens,
            "text": text,
        }
        required = _required_marking_tokens((instance_markings or {}).get(str(instance_id)))
        if required:
            document["required_markings"] = required
            document["required_marking_count"] = len(required)
        props = _keyword_prop_values(row, property_mapping, property_types)
        if props:
            document["props"] = props
        lines.append(json.dumps({"index": {"_index": INDEX_NAME, "_id": doc_id}}))
        lines.append(json.dumps(document, default=str))

    body = "\n".join(lines) + "\n"
    async with httpx.AsyncClient(auth=("admin", password), timeout=10.0) as client:
        response = await client.post(
            f"{base_url}/_bulk", content=body, headers={"Content-Type": "application/x-ndjson"}
        )
        response.raise_for_status()


async def search(
    base_url: str,
    password: str,
    *,
    principal: Principal,
    query_text: str,
    object_type: Optional[str] = None,
    object_types: Optional[list[str]] = None,
    from_: int = 0,
    size: int = 20,
    selectable_props: Optional[list[str]] = None,
    property_filters: Optional[dict[str, str]] = None,
    allow_leading_wildcards: bool = False,
    allow_regex: bool = False,
    allowed_object_types: Optional[list[str]] = None,
    held_markings: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Unified search with stable facet aggregations via ``post_filter``.

    Security filters live in the query ``filter`` (R8.6): tenant_id
    (multi-org isolation), ReBAC (`allowed_object_types`), ABAC
    entitlement tokens, and instance markings. ``object_type`` /
    ``object_types`` and ``property_filters`` are UX narrowing only —
    facet bucket counts stay scoped to the text query + those security
    filters.
    """
    # `props.*` is mapped directly to `keyword` (dynamic template above) —
    # no `.keyword` sub-field exists to aggregate on top of it, unlike a
    # `text`-mapped field. One aggregation per selectable property, not two.
    aggs: dict[str, Any] = {"object_types": {"terms": {"field": "object_type", "size": 50}}}
    for prop in selectable_props or []:
        aggs[f"prop_{prop}"] = {"terms": {"field": f"props.{prop}", "size": 20}}

    text_clause = _text_query_clause(
        query_text,
        allow_leading_wildcards=allow_leading_wildcards,
        allow_regex=allow_regex,
    )
    security_filters: list[dict[str, Any]] = [
        {"term": {"tenant_id": principal.tenant_id}},
        {"terms": {"entitlement_tokens": _principal_tokens(principal)}},
        _marking_filter(_principal_marking_tokens(held_markings)),
    ]
    if allowed_object_types is not None:
        security_filters.append(_rebac_object_type_filter(allowed_object_types))
    query: dict[str, Any] = {
        "query": {
            "bool": {
                "must": [text_clause],
                "filter": security_filters,
            }
        },
        "aggs": aggs,
        "from": from_,
        "size": size,
    }
    post_filter = _build_post_filter(
        object_type=object_type,
        object_types=object_types,
        property_filters=property_filters,
    )
    if post_filter:
        query["post_filter"] = post_filter

    async with httpx.AsyncClient(auth=("admin", password), timeout=10.0) as client:
        response = await client.post(f"{base_url}/{INDEX_NAME}/_search", json=query)
        response.raise_for_status()
        body = response.json()

    hits = body["hits"]["hits"]
    aggregations = body.get("aggregations") or {}
    facet_buckets = aggregations.get("object_types", {}).get("buckets", [])
    property_facets: dict[str, dict[str, int]] = {}
    for prop in selectable_props or []:
        buckets = aggregations.get(f"prop_{prop}", {}).get("buckets") or []
        if buckets:
            property_facets[prop] = {bucket["key"]: bucket["doc_count"] for bucket in buckets}
    return {
        "total": body["hits"]["total"]["value"],
        "results": [hit["_source"] for hit in hits],
        "facets": {bucket["key"]: bucket["doc_count"] for bucket in facet_buckets},
        "property_facets": property_facets,
    }
