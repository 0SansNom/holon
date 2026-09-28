"""Unified Search — OpenSearch integration."""

from __future__ import annotations

import json
import time
from typing import Any, Optional

import httpx

from holon_common import Principal

from .struct_values import assemble_struct_value

INDEX_NAME = "holon-search"
POLICY_VERSION = 2

_INDEX_MAPPING = {
    "mappings": {
        "dynamic_templates": [
            {
                "props_as_keyword": {
                    "path_match": "props.*",
                    "mapping": {"type": "keyword"},
                }
            },
            {
                "confidential_props_as_keyword": {
                    "path_match": "confidential_props.*",
                    "mapping": {"type": "keyword"},
                }
            },
        ],
        "properties": {
            "urn": {"type": "keyword"},
            "object_type": {"type": "keyword"},
            "tenant_id": {"type": "keyword"},
            "classification": {"type": "keyword"},
            "entitlement_tokens": {"type": "keyword"},
            "policy_version": {"type": "integer"},
            "required_markings": {"type": "keyword"},
            "required_marking_count": {"type": "integer"},
            "text": {"type": "text"},
            "confidential_text": {"type": "text"},
            "props": {"type": "object", "dynamic": True},
            "confidential_props": {"type": "object", "dynamic": True},
            "index_generation": {"type": "long"},
            "indexed_at": {"type": "double"},
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


async def delete_object_type_documents(
    base_url: str,
    password: str,
    *,
    object_type_name: str,
    tenant_id: str,
    keep_generation: int | None = None,
    indexed_before: float | None = None,
) -> None:
    """Remove indexed documents for one ObjectType.

    With ``keep_generation``, only documents from before this rebuild are
    removed: the rows just written stay, and a document indexed after
    ``indexed_before`` (a concurrent ingest) stays too.
    """
    filters: list[dict[str, Any]] = [
        {"term": {"object_type": object_type_name}},
        {"term": {"tenant_id": tenant_id}},
    ]
    if keep_generation is not None and indexed_before is not None:
        filters.append(
            {
                "bool": {
                    "should": [
                        {"range": {"indexed_at": {"lt": indexed_before}}},
                        {"bool": {"must_not": [{"exists": {"field": "indexed_at"}}]}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        )
    query: dict[str, Any] = {"bool": {"filter": filters}}
    if keep_generation is not None:
        query["bool"]["must_not"] = [{"term": {"index_generation": keep_generation}}]
    body = {"query": query, "conflicts": "proceed"}
    async with httpx.AsyncClient(auth=("admin", password), timeout=30.0) as client:
        response = await client.post(f"{base_url}/{INDEX_NAME}/_delete_by_query", json=body)
        if response.status_code not in (200, 404):
            response.raise_for_status()
        await client.post(f"{base_url}/{INDEX_NAME}/_refresh", json={})


_MAPPING_ADDITIONS = {
    "dynamic_templates": [
        {
            "props_as_keyword": {
                "path_match": "props.*",
                "mapping": {"type": "keyword"},
            }
        },
        {
            "confidential_props_as_keyword": {
                "path_match": "confidential_props.*",
                "mapping": {"type": "keyword"},
            }
        },
    ],
    "properties": {
        "required_markings": {"type": "keyword"},
        "required_marking_count": {"type": "integer"},
        "policy_version": {"type": "integer"},
        "confidential_text": {"type": "text"},
        "confidential_props": {"type": "object", "dynamic": True},
        "index_generation": {"type": "long"},
        "indexed_at": {"type": "double"},
    },
}


async def ensure_index(base_url: str, password: str) -> None:
    async with httpx.AsyncClient(auth=("admin", password), timeout=10.0) as client:
        response = await client.put(f"{base_url}/{INDEX_NAME}", json=_INDEX_MAPPING)
        if response.status_code not in (200, 400):  # 400 covers "already exists"
            response.raise_for_status()
        mapping_response = await client.put(f"{base_url}/{INDEX_NAME}/_mapping", json=_MAPPING_ADDITIONS)
        if mapping_response.status_code not in (200, 400):
            mapping_response.raise_for_status()


async def count_outside_policy(base_url: str, password: str) -> int:
    """Documents a query will not see because they lack the current policy version."""
    body = {"query": {"bool": {"must_not": [{"term": {"policy_version": POLICY_VERSION}}]}}}
    async with httpx.AsyncClient(auth=("admin", password), timeout=10.0) as client:
        response = await client.post(f"{base_url}/{INDEX_NAME}/_count", json=body)
        if response.status_code == 404:
            return 0
        response.raise_for_status()
        return int(response.json().get("count", 0))


async def index_rows(
    base_url: str,
    password: str,
    *,
    object_type_name: str,
    tenant_id: str,
    classification: str,
    property_mapping: dict,
    rows: list[dict],
    property_types: dict | None = None,
    shared_property_types: dict | None = None,
    property_classifications: dict[str, str] | None = None,
    instance_markings: dict[str, list[str]] | None = None,
    index_generation: int | None = None,
    indexed_at: float | None = None,
) -> None:
    if not rows:
        return
    generation = time.time_ns() if index_generation is None else index_generation
    stamped_at = time.time() if indexed_at is None else indexed_at
    lines: list[str] = []
    for row in rows:
        document = build_search_document(
            object_type_name=object_type_name,
            tenant_id=tenant_id,
            classification=classification,
            property_mapping=property_mapping,
            row=row,
            property_types=property_types,
            shared_property_types=shared_property_types,
            property_classifications=property_classifications,
            instance_markings=(instance_markings or {}).get(str(row["id"])),
        )
        document["index_generation"] = generation
        document["indexed_at"] = stamped_at
        lines.append(json.dumps({"index": {"_index": INDEX_NAME, "_id": document["urn"]}}))
        lines.append(json.dumps(document, default=str))

    body = "\n".join(lines) + "\n"
    async with httpx.AsyncClient(auth=("admin", password), timeout=10.0) as client:
        response = await client.post(
            f"{base_url}/_bulk", content=body, headers={"Content-Type": "application/x-ndjson"}
        )
        response.raise_for_status()


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

    Security filters: tenant, ``policy_version``, ReBAC object types, and
    instance markings. Confidential text is included only when
    ``policy.confidential_visible`` is true.
    """
    from . import policy

    include_confidential = await policy.confidential_visible(principal)
    query = build_search_query(
        principal=principal,
        query_text=query_text,
        include_confidential=include_confidential,
        object_type=object_type,
        object_types=object_types,
        from_=from_,
        size=size,
        selectable_props=selectable_props,
        property_filters=property_filters,
        allow_leading_wildcards=allow_leading_wildcards,
        allow_regex=allow_regex,
        allowed_object_types=allowed_object_types,
        held_markings=held_markings,
    )
    async with httpx.AsyncClient(auth=("admin", password), timeout=10.0) as client:
        response = await client.post(f"{base_url}/{INDEX_NAME}/_search", json=query)
        response.raise_for_status()
        body = response.json()

    hits = body["hits"]["hits"]
    aggregations = body.get("aggregations") or {}
    facet_buckets = aggregations.get("object_types", {}).get("buckets", [])
    return {
        "total": body["hits"]["total"]["value"],
        "results": [
            present_search_hit(hit["_source"], include_confidential=include_confidential) for hit in hits
        ],
        "facets": {bucket["key"]: bucket["doc_count"] for bucket in facet_buckets},
        "property_facets": _merge_property_facets(
            aggregations, list(selectable_props or []), include_confidential=include_confidential
        ),
    }
