"""Unified Search — OpenSearch integration.

Unified search does NOT post-filter results.
Each indexed document carries entitlement tokens derived from the permissions graph, and queries are filtered at the source by these tokens.

What this means in this build's actual schema:
`docker/spicedb/schema.zed`'s `object_type` permissions are all
`parent_workspace->read/write/approve` — every ObjectType has the *same*
requirement (can this principal access the workspace at all). There
is no per-document grant anywhere in this codebase to encode as a
token. The workspace access check is handled once at the API layer (`main.py`'s workspace `read` check),
before a query ever reaches OpenSearch. What genuinely varies *per
document* is the classification narrowing (`docker/opa/holon.rego`'s
`allowed_countries`) — that's what the entitlement tokens below encode,
mirrored at index time from the exact same policy already enforced
everywhere else, instead of a live per-document OPA call.
`allowed_countries` is read from OPA's own data API
(`PermissionClient.get_policy_data`) once at Knowledge startup and
threaded in as a parameter here — not a hand-copied Python literal, so
it can't silently drift from the `.rego` file. The exclusion
happens inside OpenSearch's own query (a `terms` filter), so its `total`
already reflects what's genuinely visible — never a Python list
comprehension discarding rows after the fact.

Talked to over its plain REST API via `httpx`, not an official client —
same convention already established for SpiceDB/OPA in `authz.py`.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from holon_common import Principal

INDEX_NAME = "holon-search"

_INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "urn": {"type": "keyword"},
            "object_type": {"type": "keyword"},
            "tenant_id": {"type": "keyword"},
            "classification": {"type": "keyword"},
            "entitlement_tokens": {"type": "keyword"},
            "text": {"type": "text"},
            # Keyword values for properties with render_hints including "sortable".
            "props": {"type": "object", "dynamic": True},
        }
    }
}


def _searchable_columns(property_mapping: dict, property_types: dict | None) -> list[str]:
    """Columns included in the unified `text` bag. Default searchable=True
    when render_hints is absent; omit a column only when hints are present
    and do not include ``searchable``.
    """
    columns: list[str] = []
    for prop_name, column in property_mapping.items():
        rule = (property_types or {}).get(prop_name) or {}
        hints = rule.get("render_hints")
        if hints is None or "searchable" in hints:
            columns.append(column)
    return columns


def _sortable_prop_values(row: dict, property_mapping: dict, property_types: dict | None) -> dict[str, str]:
    """Flatten sortable property values under ``props.<apiName>`` for keyword sort."""
    out: dict[str, str] = {}
    for prop_name, column in property_mapping.items():
        rule = (property_types or {}).get(prop_name) or {}
        hints = rule.get("render_hints") or []
        if "sortable" not in hints:
            continue
        value = row.get(column)
        if value is None:
            continue
        out[prop_name] = str(value)
    return out


def _entitlement_tokens_for(classification: str, allowed_countries: set[str]) -> list[str]:
    if classification != "confidential":
        return ["public-read"]
    return [f"country:{country}" for country in sorted(allowed_countries)]


def _principal_tokens(principal: Principal) -> list[str]:
    tokens = ["public-read"]
    if principal.country:
        tokens.append(f"country:{principal.country}")
    return tokens


async def ensure_index(base_url: str, password: str) -> None:
    async with httpx.AsyncClient(auth=("admin", password), timeout=10.0) as client:
        response = await client.put(f"{base_url}/{INDEX_NAME}", json=_INDEX_MAPPING)
        if response.status_code not in (200, 400):  # 400 covers "already exists"
            response.raise_for_status()


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
) -> None:
    if not rows:
        return
    tokens = _entitlement_tokens_for(classification, allowed_countries)
    text_columns = _searchable_columns(property_mapping, property_types)
    lines: list[str] = []
    for row in rows:
        instance_id = row["id"]
        doc_id = f"{object_type_name}:{tenant_id}:{instance_id}"
        text = " ".join(str(row.get(column, "")) for column in text_columns)
        document: dict[str, Any] = {
            "urn": doc_id,
            "object_type": object_type_name,
            "tenant_id": tenant_id,
            "classification": classification,
            "entitlement_tokens": tokens,
            "text": text,
        }
        props = _sortable_prop_values(row, property_mapping, property_types)
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
    from_: int = 0,
    size: int = 20,
) -> dict[str, Any]:
    """`object_type` narrows via `post_filter`, not `query.bool.filter` —
    a `post_filter` only narrows the *hits*, not the aggregation below, so
    facet counts stay stable (every ObjectType's true count for this query
    text) no matter which facet the caller currently has selected. Doing
    it the other way would collapse every non-selected facet's count to 0
    the moment one is picked, defeating the point of showing them at all.
    """
    query: dict[str, Any] = {
        "query": {
            "bool": {
                "must": [{"simple_query_string": {"query": query_text, "fields": ["text"]}}],
                "filter": [{"terms": {"entitlement_tokens": _principal_tokens(principal)}}],
            }
        },
        "aggs": {"object_types": {"terms": {"field": "object_type", "size": 50}}},
        "from": from_,
        "size": size,
    }
    if object_type:
        query["post_filter"] = {"term": {"object_type": object_type}}

    async with httpx.AsyncClient(auth=("admin", password), timeout=10.0) as client:
        response = await client.post(f"{base_url}/{INDEX_NAME}/_search", json=query)
        response.raise_for_status()
        body = response.json()

    hits = body["hits"]["hits"]
    facet_buckets = body["aggregations"]["object_types"]["buckets"]
    return {
        "total": body["hits"]["total"]["value"],
        "results": [hit["_source"] for hit in hits],
        "facets": {bucket["key"]: bucket["doc_count"] for bucket in facet_buckets},
    }
