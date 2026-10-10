"""Unified Search — OpenSearch integration."""
from __future__ import annotations

import json
import time
from typing import Any, Optional

import httpx

from holon_common import Principal

from .search_constants import INDEX_NAME, POLICY_VERSION, _INDEX_MAPPING  # noqa: F401
from .search_documents import (  # noqa: F401
    _is_property_searchable,
    _keyword_prop_values,
    _marking_filter,
    _ontology_alias_terms,
    _principal_marking_tokens,
    _rebac_object_type_filter,
    _required_marking_tokens,
    _searchable_columns,
    _sortable_prop_values,
    _struct_field_text_fragments,
    build_search_document,
    confidential_property_names,
)
from .search_query import (  # noqa: F401
    _build_post_filter,
    _merge_property_facets,
    _property_filter_clause,
    _text_fields,
    _text_query_clause,
    build_search_query,
    present_search_hit,
    selectable_property_names,
)


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

