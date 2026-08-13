"""Execution/analytics, search, and lineage — `/execute`,
`/execute/{plan_hash}/replay`, `/search`, `/lineage/{urn}`. Grouped
together because all four are read surfaces spanning the ontology
rather than a single ObjectType's CRUD, and `_mask_execution_rows`/
`_mask_group_by_aggregate` (R8.7 masking for a *shared*, cross-principal
execution-plan cache) are only ever called from `/execute`/`/replay`.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from holon_common import Principal

from .. import execution, lineage, ontology, query_log, search
from .. import core

router = APIRouter()

OPENSEARCH_URL = os.environ["HOLON_OPENSEARCH_URL"]
OPENSEARCH_PASSWORD = os.environ["HOLON_OPENSEARCH_PASSWORD"]


async def _mask_execution_rows(
    operation: str,
    object_type_urn: str,
    target_object_type_urn: Optional[str],
    principal: Principal,
    rows: list[dict],
) -> list[dict]:
    """Applies R8.7 property masking to `/execute` and `/execute/{plan_hash}/replay`
    results. Masking is applied per request for the specific calling principal
    rather than inside `execution.py`, ensuring cached plan results remain caller-agnostic
    while enforcing ABAC policy on outgoing responses. `join` rows carry two
    ObjectTypes' columns prefixed `s_`/`t_` (to keep same-named columns,
    `id` included, from colliding) — masked once per side, each against
    its own ObjectType's classifications.
    """
    if operation == "join":
        rows = await core._mask_confidential_properties(object_type_urn, principal, rows, key_prefix="s_")
        if target_object_type_urn is not None:
            rows = await core._mask_confidential_properties(target_object_type_urn, principal, rows, key_prefix="t_")
        return rows
    return await core._mask_confidential_properties(object_type_urn, principal, rows)


async def _mask_group_by_aggregate(
    object_type_urn: str,
    property_mapping: dict,
    principal: Principal,
    aggregate_property: Optional[str],
    aggregate_function: str,
    rows: list[dict],
) -> list[dict]:
    """A `group_by` aggregate over a confidential property (`sum`/`avg`/
    `min`/`max` — never `count`, which touches no property's actual value
    at all) still leaks that property's content in aggregate form to an
    ABAC-denied principal — masked the same R8.7 way a raw field is, just
    applied to the computed `aggregate` value instead of a source column.
    """
    if aggregate_function == "count" or not aggregate_property:
        return rows
    source_column = property_mapping.get(aggregate_property)
    if source_column is None or principal.country in core.allowed_countries:
        return rows
    classifications = await ontology.get_property_classifications(core.pool, object_type_urn)
    if classifications.get(source_column) != "confidential":
        return rows
    return [{**row, "aggregate": None, "_maskedFields": ["aggregate"]} for row in rows]


class ExecutionRequest(BaseModel):
    object_type: str
    operation: str = "filter"  # "filter" | "count" | "group_by" | "join" — see execution.py's module docstring
    filter_property: Optional[str] = None
    filter_value: Optional[str] = None
    # group_by
    group_by_property: Optional[str] = None
    aggregate_property: Optional[str] = None
    aggregate_function: str = "count"
    # join
    relation_name: Optional[str] = None


@router.post("/execute")
async def execute_plan(request: ExecutionRequest, principal: Principal = Depends(core.current_principal)) -> dict:
    """ExecutionPlan/Adapter abstraction — deliberately minimal (four
    operators, one built-in adapter; see `execution.py`'s module docstring
    for why). Goes through the same PDP path as every other read. Repeat
    requests for the same plan against the same DatasetVersion(s) are
    served from cache, never re-executed — `cached` in the response makes
    that directly observable.
    """
    try:
        object_type_urn = await core._object_type_urn_for(request.object_type, tenant_id=principal.tenant_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {request.object_type}")
    await core._authorize_object_type(principal, object_type_urn, "read")

    object_type = await ontology.get_object_type(core.pool, object_type_urn)
    if object_type is None:
        raise HTTPException(status_code=500, detail=f"ObjectType {object_type_urn} is not catalogued")
    property_mapping = object_type["property_mapping"]

    target_object_type_name = target_property_mapping = join_source_property = target_object_type_urn = None
    storage_kind = "foreign_key"
    bridge_dataset_name = bridge_source_column = bridge_target_column = source_id_column = None
    if request.operation == "join":
        if not request.relation_name:
            raise HTTPException(status_code=400, detail="join requires relation_name")
        relation_urn = ontology.relation_type_urn(principal.tenant_id, core.WORKSPACE_ID, request.relation_name)
        relation_type = await ontology.get_relation_type(core.pool, relation_urn)
        if relation_type is None:
            raise HTTPException(status_code=404, detail=f"unknown RelationType: {request.relation_name}")
        if relation_type["source_object_type_urn"] != object_type_urn:
            raise HTTPException(
                status_code=400,
                detail=f"RelationType {request.relation_name!r} does not originate from {request.object_type}",
            )
        storage_kind = relation_type.get("storage_kind") or "foreign_key"
        target_object_type_urn = relation_type["target_object_type_urn"]
        target_object_type_name = target_object_type_urn.rsplit(":", 1)[-1]
        await core._authorize_object_type(principal, target_object_type_urn, "read")
        target_object_type = await ontology.get_object_type(core.pool, target_object_type_urn)
        if target_object_type is None:
            raise HTTPException(status_code=500, detail=f"ObjectType {target_object_type_urn} is not catalogued")
        target_property_mapping = target_object_type["property_mapping"]

        if storage_kind == "foreign_key":
            join_source_property = relation_type["source_property"]
        elif storage_kind == "join_dataset":
            join_urn = relation_type.get("join_dataset_urn")
            bridge_source_column = relation_type.get("join_source_column")
            bridge_target_column = relation_type.get("join_target_column")
            if not join_urn or not bridge_source_column or not bridge_target_column:
                raise HTTPException(
                    status_code=400,
                    detail="join_dataset RelationType is missing join_dataset_urn / join columns",
                )
            bridge_dataset_name = join_urn.rsplit(":", 1)[-1]
            source_pk = object_type.get("primary_key") or "id"
            if source_pk not in property_mapping:
                raise HTTPException(
                    status_code=400,
                    detail=f"{request.object_type} has no primary key {source_pk!r} to join through",
                )
            source_id_column = property_mapping[source_pk]
        elif storage_kind == "object_backed":
            mid_urn = relation_type.get("mid_object_type_urn")
            mid_src_prop = relation_type.get("mid_source_property")
            mid_tgt_prop = relation_type.get("mid_target_property")
            if not mid_urn or not mid_src_prop or not mid_tgt_prop:
                raise HTTPException(
                    status_code=400,
                    detail="object_backed RelationType is missing mid ObjectType / properties",
                )
            await core._authorize_object_type(principal, mid_urn, "read")
            mid_object_type = await ontology.get_object_type(core.pool, mid_urn)
            if mid_object_type is None:
                raise HTTPException(status_code=500, detail=f"ObjectType {mid_urn} is not catalogued")
            mid_mapping = mid_object_type["property_mapping"]
            if mid_src_prop not in mid_mapping or mid_tgt_prop not in mid_mapping:
                raise HTTPException(
                    status_code=400,
                    detail=f"mid ObjectType is missing mapped properties {mid_src_prop!r}/{mid_tgt_prop!r}",
                )
            bridge_source_column = mid_mapping[mid_src_prop]
            bridge_target_column = mid_mapping[mid_tgt_prop]
            mid_name = mid_urn.rsplit(":", 1)[-1]
            if mid_name in execution._OBJECT_TYPE_TO_DATASET:
                bridge_dataset_name = execution._OBJECT_TYPE_TO_DATASET[mid_name]
            elif mid_object_type.get("source_dataset_urn"):
                bridge_dataset_name = mid_object_type["source_dataset_urn"].rsplit(":", 1)[-1]
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"mid ObjectType {mid_name!r} has no dataset mapping for analytics join",
                )
            source_pk = object_type.get("primary_key") or "id"
            if source_pk not in property_mapping:
                raise HTTPException(
                    status_code=400,
                    detail=f"{request.object_type} has no primary key {source_pk!r} to join through",
                )
            source_id_column = property_mapping[source_pk]
        else:
            raise HTTPException(status_code=400, detail=f"unsupported RelationType storage_kind: {storage_kind!r}")

    try:
        result = await execution.get_or_execute(
            core.pool,
            core.ICEBERG_CONFIG,
            tenant_id=principal.tenant_id,
            workspace_id=core.WORKSPACE_ID,
            object_type_name=request.object_type,
            object_type_urn=object_type_urn,
            property_mapping=property_mapping,
            operation=request.operation,
            filter_property=request.filter_property,
            filter_value=request.filter_value,
            group_by_property=request.group_by_property,
            aggregate_property=request.aggregate_property,
            aggregate_function=request.aggregate_function,
            relation_name=request.relation_name,
            join_source_property=join_source_property,
            target_object_type_name=target_object_type_name,
            target_property_mapping=target_property_mapping,
            storage_kind=storage_kind,
            bridge_dataset_name=bridge_dataset_name,
            bridge_source_column=bridge_source_column,
            bridge_target_column=bridge_target_column,
            source_id_column=source_id_column,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if request.operation in ("filter", "join"):
        result["results"] = await _mask_execution_rows(
            request.operation, object_type_urn, target_object_type_urn, principal, result["results"]
        )
    elif request.operation == "group_by":
        result["results"] = await _mask_group_by_aggregate(
            object_type_urn, property_mapping, principal, request.aggregate_property, request.aggregate_function,
            result["results"],
        )
    return result


@router.post("/execute/{plan_hash}/replay")
async def replay_plan(plan_hash: str, principal: Principal = Depends(core.current_principal)) -> dict:
    """Replay a previously-run plan against its pinned historical snapshot and
    report whether it reproduced bit-for-bit. Gated by the same
    `_authorize_object_type` read-check as `/execute`, resolved from the
    stored plan's own `object_type` field — a replay is still a read of
    that ObjectType's data, just of an older version of it. A frozen
    `join` plan also re-checks read on its `target_object_type` — the
    same reasoning `/execute` itself already applies when a join plan is
    first created, not relaxed just because this is a replay.
    """
    row = await core.pool.fetchrow("SELECT plan FROM execution_run WHERE plan_hash = $1", plan_hash)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no execution_run found for plan_hash {plan_hash!r}")
    plan = json.loads(row["plan"])
    operation = plan.get("operation", "filter")
    object_type_name = plan["object_type"]
    try:
        object_type_urn = await core._object_type_urn_for(object_type_name, tenant_id=principal.tenant_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {object_type_name}")
    await core._authorize_object_type(principal, object_type_urn, "read")
    target_object_type_urn = None
    if operation == "join" and plan.get("target_object_type"):
        try:
            target_object_type_urn = await core._object_type_urn_for(
                plan["target_object_type"], tenant_id=principal.tenant_id
            )
        except KeyError:
            target_object_type_urn = None
        if target_object_type_urn is not None:
            await core._authorize_object_type(principal, target_object_type_urn, "read")

    try:
        result = await execution.replay(core.pool, core.ICEBERG_CONFIG, plan_hash=plan_hash)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Same R8.7 masking `/execute` itself applies, see `_mask_execution_rows` —
    # a replay is still a read, and `execution.replay()`'s own
    # `reproducible` comparison already happened against the raw,
    # unmasked results before this runs, so masking here can't affect it.
    if operation in ("filter", "join"):
        result["result"] = await _mask_execution_rows(operation, object_type_urn, target_object_type_urn, principal, result["result"])
        result["originalResult"] = await _mask_execution_rows(
            operation, object_type_urn, target_object_type_urn, principal, result["originalResult"]
        )
    elif operation == "group_by":
        current_object_type = await ontology.get_object_type(core.pool, object_type_urn)
        property_mapping = current_object_type["property_mapping"] if current_object_type else {}
        aggregate_property = plan.get("aggregate_property")
        aggregate_function = plan.get("aggregate_function") or "count"
        result["result"] = await _mask_group_by_aggregate(
            object_type_urn, property_mapping, principal, aggregate_property, aggregate_function, result["result"]
        )
        result["originalResult"] = await _mask_group_by_aggregate(
            object_type_urn, property_mapping, principal, aggregate_property, aggregate_function, result["originalResult"]
        )
    return result


@router.get("/search")
async def unified_search(
    request: Request,
    q: str,
    object_type: Optional[str] = None,
    interface: Optional[str] = None,
    from_: int = Query(0, alias="from", ge=0),
    size: int = Query(20, ge=1, le=100),
    principal: Principal = Depends(core.current_principal),
) -> dict:
    """Entitlement-token-filtered search, no post-filtering. See
    `search.py`'s module docstring for the exact ReBAC/ABAC split.
    The ReBAC half is checked here, once, before
    any query reaches OpenSearch — the same workspace `read` permission
    every other read ultimately reduces to, checked directly against the
    `workspace` resource since a search spans every ObjectType at once
    (there's no single object_type_urn to check `_authorize_object_type`
    against). ABAC's per-document narrowing happens *inside* the
    OpenSearch query itself via entitlement tokens, not here.

    Optional ``interface`` resolves to ObjectTypes whose published
    ``implements`` expand to that interface (including via parent
    interfaces), then narrows hits with an OpenSearch ``terms``
    post_filter — same pattern as ``object_type``, without indexing
    ``implements`` on documents.
    """
    decision = await core.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=ontology.workspace_urn(principal.tenant_id, core.WORKSPACE_ID),
        permission="read",
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)

    selectable_props: list[str] = []
    search_caps = {"allow_leading_wildcards": False, "allow_regex_queries": False}
    property_filters: dict[str, str] = {}
    for key, value in request.query_params.multi_items():
        if key.startswith("prop.") and value:
            property_filters[key[5:]] = value

    interface_object_types: Optional[list[str]] = None
    if interface:
        if await ontology.get_interface_type(core.pool, principal.tenant_id, interface) is None:
            raise HTTPException(status_code=404, detail=f"unknown interface: {interface}")
        interface_object_types = await ontology.object_type_names_for_interface(
            core.pool, principal.tenant_id, interface,
        )
        if object_type and object_type not in interface_object_types:
            return {"total": 0, "results": [], "facets": {}, "property_facets": {}}
        if not interface_object_types:
            return {"total": 0, "results": [], "facets": {}, "property_facets": {}}

    if object_type:
        try:
            ot_urn = await core._object_type_urn_for(
                object_type, tenant_id=principal.tenant_id, workspace_id=core.WORKSPACE_ID
            )
            ot = await ontology.get_object_type(core.pool, ot_urn)
            if ot:
                selectable_props = search.selectable_property_names(ot.get("property_types"))
                from ..ontology.render_hints import search_capability_hints

                search_caps = search_capability_hints(ot.get("property_types"))
        except KeyError:
            selectable_props = []

    result = await search.search(
        OPENSEARCH_URL,
        OPENSEARCH_PASSWORD,
        principal=principal,
        query_text=q,
        object_type=object_type,
        object_types=None if object_type else interface_object_types,
        from_=from_,
        size=size,
        selectable_props=selectable_props or None,
        property_filters=property_filters or None,
        allow_leading_wildcards=search_caps["allow_leading_wildcards"],
        allow_regex=search_caps["allow_regex_queries"],
    )
    # Anonymized query log: tenant + query text + result count only,
    # never the principal who asked. See query_log.py's module docstring.
    await query_log.record_query(core.pool, principal.tenant_id, q, result["total"])
    return result


@router.get("/lineage/{urn:path}")
async def get_lineage(urn: str, principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """`urn` can be a dataset-version or an ObjectType, on either side of a
    `maps_to`/`derived_from` edge — same "spans everything, no single
    object_type_urn to check" situation `unified_search` above already
    handles, so this reduces to the same workspace `read` permission.
    """
    decision = await core.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=ontology.workspace_urn(principal.tenant_id, core.WORKSPACE_ID),
        permission="read",
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)
    return await lineage.edges_touching(core.pool, principal.tenant_id, urn)
