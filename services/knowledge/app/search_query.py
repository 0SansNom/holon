"""OpenSearch query construction and hit presentation."""
from __future__ import annotations

from typing import Any, Optional

from holon_common import Principal

from .search_constants import POLICY_VERSION
from .search_documents import (
    _is_confidential_property,
    _is_property_searchable,
    _marking_filter,
    _principal_marking_tokens,
    _rebac_object_type_filter,
    confidential_property_names,
)


def selectable_property_names(property_types: dict | None) -> list[str]:
    """API names (and ``struct.field`` paths) used for Search property facets."""
    from .ontology.render_hints import facet_render_hints

    return facet_render_hints(property_types)

def _text_fields(*, include_confidential: bool) -> list[str]:
    if include_confidential:
        return ["text", "confidential_text"]
    return ["text"]

def _text_query_clause(
    query_text: str,
    *,
    fields: list[str] | None = None,
    allow_leading_wildcards: bool = False,
    allow_regex: bool = False,
) -> dict[str, Any]:
    """Build the primary text clause for unified search."""
    fields = fields or ["text"]
    stripped = query_text.strip()
    if allow_regex and stripped.startswith("/") and stripped.endswith("/") and len(stripped) > 2:
        pattern = stripped[1:-1]
        clauses = [{"regexp": {field: {"value": pattern, "flags": "ALL"}}} for field in fields]
        if len(clauses) == 1:
            return clauses[0]
        return {"bool": {"should": clauses, "minimum_should_match": 1}}
    if allow_leading_wildcards:
        return {
            "query_string": {
                "query": query_text,
                "fields": fields,
                "allow_leading_wildcard": True,
                "analyze_wildcard": True,
            }
        }
    return {"simple_query_string": {"query": query_text, "fields": fields}}

def _property_filter_clause(prop: str, value: str, *, include_confidential: bool) -> dict[str, Any]:
    """Public facets live under ``props``. Confidential facets live under
    ``confidential_props`` and are queried only when the caller may see them.
    """
    public = {"term": {f"props.{prop}": value}}
    if not include_confidential:
        return public
    return {
        "bool": {
            "should": [public, {"term": {f"confidential_props.{prop}": value}}],
            "minimum_should_match": 1,
        }
    }

def _build_post_filter(
    *,
    object_type: Optional[str] = None,
    object_types: Optional[list[str]] = None,
    property_filters: Optional[dict[str, str]] = None,
    include_confidential: bool = False,
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
        clauses.append(_property_filter_clause(prop, value, include_confidential=include_confidential))
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"bool": {"filter": clauses}}

def build_search_query(
    *,
    principal: Principal,
    query_text: str,
    include_confidential: bool,
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
    """OpenSearch body. Documents without ``policy_version`` 2 do not match.

    Country clearance is not baked into the document. Confidential text
    and facets are queried only when ``include_confidential`` is true.
    """
    fields = _text_fields(include_confidential=include_confidential)
    aggs: dict[str, Any] = {"object_types": {"terms": {"field": "object_type", "size": 50}}}
    for prop in selectable_props or []:
        aggs[f"prop_{prop}"] = {"terms": {"field": f"props.{prop}", "size": 20}}
        if include_confidential:
            aggs[f"cprop_{prop}"] = {"terms": {"field": f"confidential_props.{prop}", "size": 20}}

    text_clause = _text_query_clause(
        query_text,
        fields=fields,
        allow_leading_wildcards=allow_leading_wildcards,
        allow_regex=allow_regex,
    )
    security_filters: list[dict[str, Any]] = [
        {"term": {"tenant_id": principal.tenant_id}},
        {"term": {"policy_version": POLICY_VERSION}},
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
        include_confidential=include_confidential,
    )
    if post_filter:
        query["post_filter"] = post_filter
    return query

def present_search_hit(source: dict[str, Any], *, include_confidential: bool) -> dict[str, Any]:
    """Response document. Confidential fields stay out of the payload
    unless this principal may see them, in which case they are folded
    into ``text`` and ``props`` for the existing search UI.
    """
    document = dict(source)
    if include_confidential:
        extra = document.get("confidential_text") or ""
        if extra:
            public = document.get("text") or ""
            document["text"] = f"{public} {extra}".strip() if public else extra
        secret_props = document.get("confidential_props") or {}
        if secret_props:
            props = dict(document.get("props") or {})
            props.update(secret_props)
            document["props"] = props
    document.pop("confidential_text", None)
    document.pop("confidential_props", None)
    document.pop("index_generation", None)
    document.pop("indexed_at", None)
    return document

def _merge_property_facets(
    aggregations: dict[str, Any],
    selectable_props: list[str],
    *,
    include_confidential: bool,
) -> dict[str, dict[str, int]]:
    property_facets: dict[str, dict[str, int]] = {}
    for prop in selectable_props:
        counts: dict[str, int] = {}
        for agg_name in (f"prop_{prop}", f"cprop_{prop}") if include_confidential else (f"prop_{prop}",):
            for bucket in (aggregations.get(agg_name) or {}).get("buckets") or []:
                counts[bucket["key"]] = counts.get(bucket["key"], 0) + bucket["doc_count"]
        if counts:
            property_facets[prop] = counts
    return property_facets

