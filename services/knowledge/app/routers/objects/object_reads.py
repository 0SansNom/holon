"""Export, relation traversal, and instance-level link-analysis graph —
generic over any ObjectType via `core._type_handle` → `fetch_generic`,
the same path self-serve ObjectTypes use.

`export_objects` is registered before `generic.py`'s routes combine in
(`objects/__init__.py`): Starlette matches routes in registration order,
so `/objects/{object_type}/export` must precede `generic.py`'s
`/objects/{object_type}/{instance_id}` to avoid matching `"export"` as
the instance id.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from holon_common import HolonError, Principal, build_urn

from ... import export_format_registry, ontology
from ... import core
from ...actions import list_instance_timeline
from ...actions.declarative import _write_instance_edits
from .generic import _merge_declarative_edits
from .paging_deps import page_response, paging_query
from ...paging import MAX_WALK_ITEMS

router = APIRouter()

MAX_HOPS = 3
DEFAULT_HOPS = 2
MAX_NODES = 200


async def _require_handle(object_type: str, tenant_id: str = core.TENANT_ID) -> dict:
    handle = await core._type_handle(object_type, tenant_id)
    if handle is None:
        raise HolonError.internal('ObjectTypeNotCatalogued', f"ObjectType {object_type} is not catalogued")
    return handle


@router.get("/objects/{object_type}/export")
async def export_objects(
    object_type: str, format: str = "json", principal: Principal = Depends(core.current_principal)
) -> Response:
    """Export-format plugin endpoint — `format=json`
    is always available (the same data every list endpoint already
    returns); any other value must be a registered, active export-format
    plugin. Reads through the identical `_resolve_many` every list
    endpoint uses — confidential property masking included — export is a serialization
    concern layered on top, never a second read path. Registered *before*
    the six type-specific `/objects/{Type}/{id}` routes below: Starlette
    matches routes in registration order, so `/objects/Customer/export`
    must precede `/objects/Customer/{customer_id}` to prevent `"export"`
    from being captured as `{customer_id}`.
    """
    try:
        object_type_urn = await core._object_type_urn_for(object_type, tenant_id=principal.tenant_id)
    except KeyError:
        raise HolonError.not_found('ObjectTypeNotFound', f"unknown ObjectType {object_type!r}")
    await core._authorize_object_type(principal, object_type_urn, "read")
    handle = await _require_handle(object_type, principal.tenant_id)
    rows = await core._resolve_many(object_type, principal.tenant_id, handle["fetch_fn"], principal=principal)

    if format == "json":
        return JSONResponse(content=jsonable_encoder(rows))

    found = await export_format_registry.find_active_format(core.pool, format)
    if found is None:
        raise HolonError.invalid_argument('UnknownExportFormat', f"unknown export format {format!r}", format=format)
    plugin, content_type = found
    body = plugin.serialize(jsonable_encoder(rows))
    return Response(content=body, media_type=content_type)


def _coerce_instance_id(object_type: str, instance_id: str):
    """The graph/links endpoints serve any ObjectType generically, so the
    id type isn't known ahead of time — try int, fall back to string, the
    same discipline `resolver.fetch_generic` already applies on its own end.
    """
    try:
        return int(instance_id)
    except ValueError:
        return instance_id


def _to_node(object_type: str, instance_id, row: dict, *, hop: int) -> dict:
    return {
        "id": f"{object_type}:{instance_id}",
        "objectType": object_type,
        "instanceId": instance_id,
        "label": ontology.title_of(row),
        "hop": hop,
        "degraded": bool(row.get("degraded", False)),
        "maskedFields": row.get("_maskedFields", []),
    }


async def _traverse_neighborhood(
    object_type: str, instance_id, root_row: dict, hops: int, principal: Principal,
) -> dict:
    """N-hop instance-neighborhood BFS over every RelationType (seeded
    and self-serve alike, via `_resolve_relation_neighbors`) — Vertex-
    style link analysis, a different granularity entirely from the
    schema/dataset-level `/lineage` endpoint (`routers/execute.py`),
    which has no notion of a specific instance.

    Edges always follow the RelationType's own `source_object_type` ->
    `target_object_type` direction, regardless of which direction BFS
    discovered them from; `direction` records *how* the edge was found
    (`toward_one`/`toward_many`, see `_resolve_relation_neighbors`) — not
    indexed on the fan-out column today, the real reason `hops`/
    `MAX_NODES` are both hard-capped rather than left open.

    A node `_resolve_one` can't return (marking-denied/missing) is
    simply omitted, never aborts the rest of the traversal — the BFS
    equivalent of the "degrade instead of 500" discipline `_resolve_one`
    itself already applies to a federated-fallback miss.
    """
    relation_types = await ontology.list_relation_types(core.pool, principal.tenant_id)
    authorized_types = {object_type}
    property_mapping_cache: dict[str, dict] = {}

    def node_id(ot: str, iid) -> str:
        return f"{ot}:{iid}"

    root_id = node_id(object_type, instance_id)
    nodes = {root_id: _to_node(object_type, instance_id, root_row, hop=0)}
    edges: list[dict] = []
    truncated = False
    frontier = [(object_type, instance_id, root_row)]

    for hop in range(1, hops + 1):
        next_frontier = []
        for current_type, current_id, current_row in frontier:
            for relation in relation_types:
                result = await core._resolve_relation_neighbors(
                    relation, current_type, current_id, current_row, principal,
                    authorized_types=authorized_types, property_mapping_cache=property_mapping_cache,
                )
                if result is None:
                    continue
                neighbor_type, neighbor_rows, direction = result

                for neighbor_row in neighbor_rows:
                    if len(nodes) >= MAX_NODES:
                        truncated = True
                        break
                    neighbor_id = neighbor_row["id"]
                    n_id = node_id(neighbor_type, neighbor_id)
                    edges.append({
                        "id": f"e{len(edges)}",
                        "source": node_id(current_type, current_id) if direction == "toward_one" else n_id,
                        "target": n_id if direction == "toward_one" else node_id(current_type, current_id),
                        "relation": relation["name"],
                        "direction": direction,
                    })
                    if n_id not in nodes:
                        nodes[n_id] = _to_node(neighbor_type, neighbor_id, neighbor_row, hop=hop)
                        if hop < hops:
                            next_frontier.append((neighbor_type, neighbor_id, neighbor_row))
        frontier = next_frontier
        if truncated or not frontier:
            break

    return {"root": root_id, "nodes": list(nodes.values()), "edges": edges, "truncated": truncated}


@router.get("/objects/{object_type}/{instance_id}/graph")
async def get_object_graph(
    object_type: str, instance_id: str, hops: int = DEFAULT_HOPS,
    principal: Principal = Depends(core.current_principal),
) -> dict:
    """Instance-level link-analysis graph — see the module docstring's
    top note and `_traverse_neighborhood` for the full design. `hops` is
    clamped, not rejected, past `MAX_HOPS`: same "degrade rather than
    fail the whole request" discipline every read choke point in this
    service already applies to bad-but-not-malicious input.
    """
    hops = max(1, min(hops, MAX_HOPS))
    handle = await core._type_handle(object_type)
    if handle is None:
        raise HolonError.not_found('ObjectTypeNotFound', f"unknown ObjectType {object_type!r}")
    await core._authorize_object_type(principal, handle["urn"], "read")

    typed_id = _coerce_instance_id(object_type, instance_id)
    root_row = await core._resolve_one(
        object_type, principal.tenant_id, typed_id, handle["fetch_fn"], handle["id_kwarg"], principal=principal,
    )
    if root_row is None:
        raise HolonError.not_found('ObjectInstanceNotFound', f"{object_type}/{instance_id} not found", object_type=object_type, instance_id=instance_id)

    return await _traverse_neighborhood(object_type, root_row["id"], root_row, hops, principal)


async def _read_object_link(
    object_type: str,
    instance_id: str,
    link_name: str,
    principal: Principal,
    *,
    page_size: int,
    cursor: Optional[str],
) -> dict:
    """Shared link read used by the GET route and post-mutation refresh."""
    handle = await core._type_handle(object_type)
    if handle is None:
        raise HolonError.not_found('ObjectTypeNotFound', f"unknown ObjectType {object_type!r}")
    await core._authorize_object_type(principal, handle["urn"], "read")

    typed_id = _coerce_instance_id(object_type, instance_id)
    current_row = await core._resolve_one(
        object_type, principal.tenant_id, typed_id, handle["fetch_fn"], handle["id_kwarg"], principal=principal,
    )
    if current_row is None:
        raise HolonError.not_found('ObjectInstanceNotFound', f"{object_type}/{instance_id} not found", object_type=object_type, instance_id=instance_id)

    # Overlay FK edits (link write/unlink) onto the row before traversal so
    # a freshly-written link is visible on the next GET without Iceberg sync.
    current_row = (await _merge_declarative_edits([current_row], object_type, principal.tenant_id))[0]

    relation_types = await ontology.list_relation_types(core.pool, principal.tenant_id)
    matched = core._find_relation_by_link_name(relation_types, object_type, link_name)
    if matched is None:
        raise HolonError.not_found('LinkNotFound', f"unknown link {link_name!r} on {object_type!r}", link_name=link_name, object_type=object_type)

    instance_pk = current_row.get("id")
    ot_def = await ontology.get_object_type(core.pool, handle["urn"])
    if ot_def and ot_def.get("primary_key"):
        pk = ot_def["primary_key"]
        instance_pk = current_row.get(pk, current_row.get(ot_def["property_mapping"].get(pk, "id"), instance_pk))

    result = await core._resolve_relation_neighbors(
        matched, object_type, instance_pk, current_row, principal,
        authorized_types={object_type}, property_mapping_cache={},
    )
    if result is None:
        return {
            "relation": matched["name"],
            "direction": None,
            "cardinality": matched["cardinality"],
            "storage_kind": matched.get("storage_kind") or "foreign_key",
            "data": [],
            "nextPageToken": None,
            "pageSize": page_size,
        }
    neighbor_type, neighbor_rows, direction = result
    neighbor_ot = await ontology.get_object_type(
        core.pool, ontology.object_type_urn(principal.tenant_id, core.WORKSPACE_ID, neighbor_type)
    )
    items = []
    link_objects = []
    for row in neighbor_rows:
        item = dict(row)
        link_obj = item.pop("_link_object", None)
        link_ot = item.pop("_link_object_type", None)
        item["title"] = ontology.title_of(item, neighbor_ot)
        items.append(item)
        if link_obj is not None:
            link_objects.append({"object_type": link_ot, "object": link_obj})
    paged = page_response(items, page_size=page_size, cursor=cursor)
    # Keep link_objects aligned with the *full* neighbor set for now — they
    # are metadata for object-backed relations, not a second page stream.
    payload = {
        "relation": matched["name"],
        "direction": direction,
        "cardinality": matched["cardinality"],
        "storage_kind": matched.get("storage_kind") or "foreign_key",
        "data": paged["data"],
        "nextPageToken": paged["nextPageToken"],
        "pageSize": paged["pageSize"],
    }
    if link_objects:
        payload["link_objects"] = link_objects
    return payload


@router.get("/objects/{object_type}/{instance_id}/links/{link_name}")
async def get_object_link(
    object_type: str,
    instance_id: str,
    link_name: str,
    principal: Principal = Depends(core.current_principal),
    page: tuple[int, Optional[str]] = Depends(paging_query),
) -> dict:
    """The named single-link accessor — Foundry's real `customer.orders`/
    `order.customer` access pattern, distinct from `get_object_graph`'s
    N-hop visualization: exactly one RelationType's related instance(s),
    addressed by name instead of walked (`core._find_relation_by_link_name`
    resolves `link_name`). Works for any ObjectType via `core._type_handle`,
    same as the graph endpoint above.
    """
    page_size, cursor = page
    return await _read_object_link(
        object_type, instance_id, link_name, principal, page_size=page_size, cursor=cursor,
    )


class LinkWriteRequest(BaseModel):
    target_id: Optional[object] = None


async def _mutate_link(
    *,
    object_type: str,
    instance_id: str,
    link_name: str,
    principal: Principal,
    target_id: object,
    unlink: bool,
) -> dict:
    """Link write/unlink for foreign_key, join_dataset, and object_backed.

    FK: `object_instance_edit` overlay on the source OT property.
    join_dataset / object_backed: `relation_link_overlay` add/delete pairs
    (visible on next GET links; analytics `/execute` stays Iceberg-synced).
    """
    from ... import link_overlays

    handle = await core._type_handle(object_type)
    if handle is None:
        raise HolonError.not_found('ObjectTypeNotFound', f"unknown ObjectType {object_type!r}")
    await core._authorize_object_type(principal, handle["urn"], "write")

    typed_id = _coerce_instance_id(object_type, instance_id)
    current_row = await core._resolve_one(
        object_type, principal.tenant_id, typed_id, handle["fetch_fn"], handle["id_kwarg"], principal=principal,
    )
    if current_row is None:
        raise HolonError.not_found('ObjectInstanceNotFound', f"{object_type}/{instance_id} not found", object_type=object_type, instance_id=instance_id)

    relation_types = await ontology.list_relation_types(core.pool, principal.tenant_id)
    matched = core._find_relation_by_link_name(relation_types, object_type, link_name)
    if matched is None:
        raise HolonError.not_found('LinkNotFound', f"unknown link {link_name!r} on {object_type!r}", link_name=link_name, object_type=object_type)

    storage = matched.get("storage_kind") or "foreign_key"
    source_name = matched["source_object_type_urn"].rsplit(":", 1)[-1]
    target_name = matched["target_object_type_urn"].rsplit(":", 1)[-1]
    at = datetime.now(timezone.utc)
    action_urn = build_urn(
        principal.tenant_id, core.WORKSPACE_ID, "action", f"{'unlink' if unlink else 'link'}.{matched['name']}"
    )

    if storage == "foreign_key":
        if object_type != source_name:
            raise HolonError.invalid_argument('LinkWriteWrongEnd', "link write/unlink must be invoked from the FK-holding (source) ObjectType")
        if not unlink and target_id is None:
            raise HolonError.invalid_argument('LinkTargetRequired', "target_id is required to link")
        fk_property = matched["source_property"]
        async with core.pool.acquire() as conn:
            async with conn.transaction():
                await _write_instance_edits(
                    conn,
                    principal.tenant_id,
                    object_type,
                    str(typed_id),
                    {fk_property: None if unlink else target_id},
                    action_urn=action_urn,
                    actor=principal,
                    at=at,
                )
        return await _read_object_link(
            object_type, instance_id, link_name, principal,
            page_size=MAX_WALK_ITEMS, cursor=None,
        )

    if storage not in ("join_dataset", "object_backed"):
        raise HolonError.from_http(
            501,
            f"link write/unlink is not supported for storage {storage!r}",
            error_name="NotImplemented",
        )

    if target_id is None:
        raise HolonError.invalid_argument(
            "InvalidArgument",
            "target_id is required to link/unlink join_dataset and object_backed relations",
        )

    # Normalize orientation: overlay always stores (source_ot_id, target_ot_id).
    if object_type == source_name:
        source_id, neighbor_id = typed_id, target_id
    elif object_type == target_name:
        source_id, neighbor_id = target_id, typed_id
    else:
        raise HolonError.invalid_argument('ObjectTypeNotOnRelation', "object type is not an end of this relation")

    mid_id = None
    if storage == "object_backed":
        mid_id = f"overlay:{source_id}:{neighbor_id}"

    async with core.pool.acquire() as conn:
        async with conn.transaction():
            await link_overlays.upsert_link(
                conn,
                tenant_id=principal.tenant_id,
                relation_urn=matched["urn"],
                source_id=str(source_id),
                target_id=str(neighbor_id),
                op="delete" if unlink else "add",
                set_by_urn=principal.urn,
                set_at=at,
                mid_id=mid_id,
            )

    return await _read_object_link(
        object_type, instance_id, link_name, principal,
        page_size=MAX_WALK_ITEMS, cursor=None,
    )


@router.put("/objects/{object_type}/{instance_id}/links/{link_name}")
async def put_object_link(
    object_type: str,
    instance_id: str,
    link_name: str,
    request: LinkWriteRequest,
    principal: Principal = Depends(core.current_principal),
) -> dict:
    """Foundry-style link write — set FK or add join/mid overlay pair."""
    return await _mutate_link(
        object_type=object_type,
        instance_id=instance_id,
        link_name=link_name,
        principal=principal,
        target_id=request.target_id,
        unlink=False,
    )


@router.delete("/objects/{object_type}/{instance_id}/links/{link_name}")
async def delete_object_link(
    object_type: str,
    instance_id: str,
    link_name: str,
    principal: Principal = Depends(core.current_principal),
    target_id: Optional[str] = Query(None),
) -> dict:
    """Foundry-style unlink — clear FK overlay, or remove a join/mid pair.

    For join_dataset / object_backed, pass `target_id` (query) identifying
    the other end of the pair to remove.
    """
    coerced_target: object = target_id
    if target_id is not None:
        try:
            coerced_target = int(target_id) if target_id.isdigit() else target_id
        except (TypeError, ValueError):
            coerced_target = target_id
    return await _mutate_link(
        object_type=object_type,
        instance_id=instance_id,
        link_name=link_name,
        principal=principal,
        target_id=coerced_target,
        unlink=True,
    )


@router.get("/objects/{object_type}/{instance_id}/timeline")
async def get_object_timeline(
    object_type: str, instance_id: str, principal: Principal = Depends(core.current_principal),
) -> list[dict]:
    """The real event history for one instance — every Action actually
    applied (`action_invocation`) merged with the request/decision
    lifecycle of every high-risk proposal (`action_approval`), via
    `actions.list_instance_timeline`. Nothing new is captured for this;
    both tables are already written from the single shared `_apply_now`/
    `approve_action` code path (`actions/__init__.py`) regardless of
    which endpoint or Action Type triggered them. Works for any
    ObjectType via `core._type_handle`, same as the graph/links endpoints
    above.
    """
    handle = await core._type_handle(object_type)
    if handle is None:
        raise HolonError.not_found('ObjectTypeNotFound', f"unknown ObjectType {object_type!r}")
    await core._authorize_object_type(principal, handle["urn"], "read")

    typed_id = _coerce_instance_id(object_type, instance_id)
    current_row = await core._resolve_one(
        object_type, principal.tenant_id, typed_id, handle["fetch_fn"], handle["id_kwarg"], principal=principal,
    )
    if current_row is None:
        raise HolonError.not_found('ObjectInstanceNotFound', f"{object_type}/{instance_id} not found", object_type=object_type, instance_id=instance_id)

    instance_urn = build_urn(principal.tenant_id, core.WORKSPACE_ID, "instance", f"{object_type}/{instance_id}")
    return await list_instance_timeline(core.pool, principal.tenant_id, instance_urn)
