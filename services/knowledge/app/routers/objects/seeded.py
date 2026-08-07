"""Reads for the six boot-seeded ObjectTypes (Customer/Order/
ProductReview/SupportTicket/Supplier/InventoryLevel) — list/get pairs,
relation traversal, export, and the instance-level link-analysis graph.

`export_objects` is registered first, before the typed routes:
Starlette matches routes in registration order, so
`/objects/Customer/export` must precede `/objects/Customer/{customer_id}`
to avoid matching `"export"` as `{customer_id}`. `objects/__init__.py`
combines this router with `generic.py`'s in that exact order — moving
either file's routes independently would break that.

`get_object_graph` looks generic (`/objects/{object_type}/{instance_id}/graph`
accepts any name) but only actually works for these six: it looks
`object_type` up in `core.OBJECT_TYPE_URNS`/`core.FETCH_FNS`/
`core.ID_KWARGS`, which are static dicts of exactly the seeded six, never
grown for a self-serve ObjectType — a real, existing gap (no graph
traversal for self-serve types), not something this split changes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from holon_common import Principal

from ... import export_format_registry, ontology, resolver
from ... import core
from ...actions import get_account_status, get_credit_holds

router = APIRouter()

MAX_HOPS = 3
DEFAULT_HOPS = 2
MAX_NODES = 200

_LABELS = {
    "Customer": lambda r: r.get("name") or f"Customer #{r['id']}",
    "Order": lambda r: f"Order #{r['id']} — {r.get('product') or '?'}",
    "SupportTicket": lambda r: r.get("subject") or f"Ticket #{r['id']}",
    "ProductReview": lambda r: f"Review #{r['id']} ({r.get('rating', '?')}★)",
    "Supplier": lambda r: r.get("name") or f"Supplier #{r['id']}",
    "InventoryLevel": lambda r: f"{r.get('id')} @ {r.get('warehouse', '?')}",
}


async def _merge_action_overlays(rows: list[dict]) -> list[dict]:
    customer_ids = [row["id"] for row in rows]
    holds = await get_credit_holds(core.pool, customer_ids)
    statuses = await get_account_status(core.pool, customer_ids)
    for row in rows:
        hold = holds.get(row["id"])
        row["credit_hold"] = bool(hold["on_hold"]) if hold else False
        row["credit_hold_reason"] = hold["reason"] if hold else None
        status = statuses.get(row["id"])
        row["account_closed"] = bool(status["closed"]) if status else False
        row["account_closed_reason"] = status["reason"] if status else None
    return rows


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
    if object_type not in core.OBJECT_TYPE_URNS:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType {object_type!r}")
    await core._authorize_object_type(principal, core.OBJECT_TYPE_URNS[object_type], "read")
    rows = await core._resolve_many(object_type, principal.tenant_id, core.FETCH_FNS[object_type], principal=principal)

    if format == "json":
        return JSONResponse(content=jsonable_encoder(rows))

    found = await export_format_registry.find_active_format(core.pool, format)
    if found is None:
        raise HTTPException(status_code=400, detail=f"unknown export format {format!r}")
    plugin, content_type = found
    body = plugin.serialize(jsonable_encoder(rows))
    return Response(content=body, media_type=content_type)


@router.get("/objects/Customer")
async def list_customers(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    await core._authorize_object_type(principal, core.CUSTOMER_OBJECT_TYPE_URN, "read")
    rows = await core._resolve_many("Customer", principal.tenant_id, resolver.fetch_customers, principal=principal)
    return await _merge_action_overlays(rows)


@router.get("/objects/Customer/{customer_id}")
async def get_customer(
    customer_id: int, as_of: Optional[datetime] = None, principal: Principal = Depends(core.current_principal)
) -> dict:
    await core._authorize_object_type(principal, core.CUSTOMER_OBJECT_TYPE_URN, "read")
    row = await core._resolve_one(
        "Customer", principal.tenant_id, customer_id, resolver.fetch_customers, "customer_id",
        as_of=as_of, principal=principal,
    )
    if row is None:
        detail = f"Customer/{customer_id} not found"
        if as_of is not None:
            detail += f" as of {as_of.isoformat()} (no history recorded yet at that time)"
        raise HTTPException(status_code=404, detail=detail)
    # Historical read reports the object's own state as of that time —
    # applying *today's* action overlays (credit hold/account closed) to a
    # past snapshot would mix two different points in time into one answer.
    if as_of is not None:
        return row
    return (await _merge_action_overlays([row]))[0]


@router.get("/objects/Customer/{customer_id}/orders")
async def get_customer_orders(customer_id: int, principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """Relation traversal (1 to 3 hops) over
    `ontology.RELATION_TYPES` — authorized against what's actually being
    returned (Order), not against Customer.
    """
    await core._authorize_object_type(principal, core.ORDER_OBJECT_TYPE_URN, "read")
    if not await core._resolve_one("Customer", principal.tenant_id, customer_id, resolver.fetch_customers, "customer_id", principal=principal):
        raise HTTPException(status_code=404, detail=f"Customer/{customer_id} not found")
    return await core._resolve_many(
        "Order", principal.tenant_id, resolver.fetch_orders, principal=principal,
        filter_column="customer_id", filter_kwarg="customer_id", filter_value=customer_id,
    )


@router.get("/objects/Customer/{customer_id}/tickets")
async def get_customer_tickets(customer_id: int, principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """Second relation traversal, `SupportTicket.customer` — same pattern,
    a structurally different source (MongoDB) underneath.
    """
    await core._authorize_object_type(principal, core.SUPPORT_TICKET_OBJECT_TYPE_URN, "read")
    if not await core._resolve_one("Customer", principal.tenant_id, customer_id, resolver.fetch_customers, "customer_id", principal=principal):
        raise HTTPException(status_code=404, detail=f"Customer/{customer_id} not found")
    return await core._resolve_many(
        "SupportTicket", principal.tenant_id, resolver.fetch_support_tickets, principal=principal,
        filter_column="customer_id", filter_kwarg="customer_id", filter_value=customer_id,
    )


@router.get("/objects/Order")
async def list_orders(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    await core._authorize_object_type(principal, core.ORDER_OBJECT_TYPE_URN, "read")
    return await core._resolve_many("Order", principal.tenant_id, resolver.fetch_orders, principal=principal)


@router.get("/objects/Order/{order_id}")
async def get_order(
    order_id: int, as_of: Optional[datetime] = None, principal: Principal = Depends(core.current_principal)
) -> dict:
    await core._authorize_object_type(principal, core.ORDER_OBJECT_TYPE_URN, "read")
    row = await core._resolve_one("Order", principal.tenant_id, order_id, resolver.fetch_orders, "order_id", as_of=as_of, principal=principal)
    if row is None:
        detail = f"Order/{order_id} not found"
        if as_of is not None:
            detail += f" as of {as_of.isoformat()} (no history recorded yet at that time)"
        raise HTTPException(status_code=404, detail=detail)
    return row


@router.get("/objects/Order/{order_id}/reviews")
async def get_order_reviews(order_id: int, principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """Third relation traversal, `ProductReview.order` — and the first one
    that doesn't start at Customer: the graph now chains two hops
    (Customer -> Order -> ProductReview), not just a one-level fan-out.
    """
    await core._authorize_object_type(principal, core.PRODUCT_REVIEW_OBJECT_TYPE_URN, "read")
    if not await core._resolve_one("Order", principal.tenant_id, order_id, resolver.fetch_orders, "order_id", principal=principal):
        raise HTTPException(status_code=404, detail=f"Order/{order_id} not found")
    return await core._resolve_many(
        "ProductReview", principal.tenant_id, resolver.fetch_reviews, principal=principal,
        filter_column="order_id", filter_kwarg="order_id", filter_value=order_id,
    )


@router.get("/objects/ProductReview")
async def list_reviews(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    await core._authorize_object_type(principal, core.PRODUCT_REVIEW_OBJECT_TYPE_URN, "read")
    return await core._resolve_many("ProductReview", principal.tenant_id, resolver.fetch_reviews, principal=principal)


@router.get("/objects/ProductReview/{review_id}")
async def get_review(review_id: int, principal: Principal = Depends(core.current_principal)) -> dict:
    await core._authorize_object_type(principal, core.PRODUCT_REVIEW_OBJECT_TYPE_URN, "read")
    row = await core._resolve_one("ProductReview", principal.tenant_id, review_id, resolver.fetch_reviews, "review_id", principal=principal)
    if row is None:
        raise HTTPException(status_code=404, detail=f"ProductReview/{review_id} not found")
    return row


@router.get("/objects/SupportTicket")
async def list_support_tickets(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    await core._authorize_object_type(principal, core.SUPPORT_TICKET_OBJECT_TYPE_URN, "read")
    return await core._resolve_many("SupportTicket", principal.tenant_id, resolver.fetch_support_tickets, principal=principal)


@router.get("/objects/SupportTicket/{ticket_id}")
async def get_support_ticket(ticket_id: int, principal: Principal = Depends(core.current_principal)) -> dict:
    await core._authorize_object_type(principal, core.SUPPORT_TICKET_OBJECT_TYPE_URN, "read")
    row = await core._resolve_one("SupportTicket", principal.tenant_id, ticket_id, resolver.fetch_support_tickets, "ticket_id", principal=principal)
    if row is None:
        raise HTTPException(status_code=404, detail=f"SupportTicket/{ticket_id} not found")
    return row


@router.get("/objects/Supplier")
async def list_suppliers(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    await core._authorize_object_type(principal, core.SUPPLIER_OBJECT_TYPE_URN, "read")
    return await core._resolve_many("Supplier", principal.tenant_id, resolver.fetch_suppliers, principal=principal)


@router.get("/objects/Supplier/{supplier_id}")
async def get_supplier(supplier_id: int, principal: Principal = Depends(core.current_principal)) -> dict:
    await core._authorize_object_type(principal, core.SUPPLIER_OBJECT_TYPE_URN, "read")
    row = await core._resolve_one("Supplier", principal.tenant_id, supplier_id, resolver.fetch_suppliers, "supplier_id", principal=principal)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Supplier/{supplier_id} not found")
    return row


@router.get("/objects/InventoryLevel")
async def list_inventory_levels(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    await core._authorize_object_type(principal, core.INVENTORY_LEVEL_OBJECT_TYPE_URN, "read")
    return await core._resolve_many("InventoryLevel", principal.tenant_id, resolver.fetch_inventory_levels, principal=principal)


@router.get("/objects/InventoryLevel/{sku}")
async def get_inventory_level(sku: str, principal: Principal = Depends(core.current_principal)) -> dict:
    await core._authorize_object_type(principal, core.INVENTORY_LEVEL_OBJECT_TYPE_URN, "read")
    row = await core._resolve_one("InventoryLevel", principal.tenant_id, sku, resolver.fetch_inventory_levels, "sku", principal=principal)
    if row is None:
        raise HTTPException(status_code=404, detail=f"InventoryLevel/{sku} not found")
    return row


def _coerce_instance_id(object_type: str, instance_id: str):
    """Unlike every typed route above (which lets FastAPI coerce
    `customer_id: int` etc. straight from the path), this one endpoint's
    `instance_id` path param must serve 5 int-keyed types and 1
    string-keyed type (`InventoryLevel`/SKU) — no single Python type
    annotation covers both, so the coercion happens here instead.
    """
    if object_type == "InventoryLevel":
        return instance_id
    try:
        return int(instance_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"{object_type}/{instance_id} not found")


def _to_node(object_type: str, instance_id, row: dict, *, hop: int) -> dict:
    return {
        "id": f"{object_type}:{instance_id}",
        "objectType": object_type,
        "instanceId": instance_id,
        "label": _LABELS.get(object_type, lambda r: str(r.get("id")))(row),
        "hop": hop,
        "degraded": bool(row.get("degraded", False)),
        "maskedFields": row.get("_maskedFields", []),
    }


async def _traverse_neighborhood(
    object_type: str, instance_id, root_row: dict, hops: int, principal: Principal,
) -> dict:
    """N-hop instance-neighborhood BFS over the seeded (and any
    dynamically-created) RelationTypes — Vertex-style link analysis, a
    different granularity entirely from the schema/dataset-level
    `/lineage` endpoint (`routers/execute.py`), which has no notion of a
    specific instance.

    Edges always follow the RelationType's own `source_object_type` ->
    `target_object_type` direction, regardless of which direction BFS
    discovered them from; `direction` records *how* the edge was found
    (`toward_one`: the current row's own FK value points at its one
    parent, cheap/PK-indexed; `toward_many`: fan out to every row whose
    FK column matches the current instance, via the same generic
    `filter_column` `_resolve_many` already exposes — not indexed on
    that column today, the real reason `hops`/`MAX_NODES` are both
    hard-capped rather than left open).

    A node `_resolve_one` can't return (marking-denied/missing) is
    simply omitted, never aborts the rest of the traversal — the BFS
    equivalent of the "degrade instead of 500" discipline `_resolve_one`
    itself already applies to a federated-fallback miss.
    """
    relation_types = await ontology.list_relation_types(core.pool, principal.tenant_id)
    urn_to_name = {urn: name for name, urn in core.OBJECT_TYPE_URNS.items()}
    authorized_types = {object_type}
    property_mapping_cache: dict[str, dict] = {}

    async def storage_column(source_object_type_urn: str, source_property: str) -> Optional[str]:
        mapping = property_mapping_cache.get(source_object_type_urn)
        if mapping is None:
            source_object_type = await ontology.get_object_type(core.pool, source_object_type_urn)
            if source_object_type is None:
                return None
            mapping = source_object_type["property_mapping"]
            property_mapping_cache[source_object_type_urn] = mapping
        return mapping.get(source_property)

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
                source_name = urn_to_name.get(relation["source_object_type_urn"])
                target_name = urn_to_name.get(relation["target_object_type_urn"])
                # A relation touching an ObjectType outside the 6 this
                # endpoint knows how to fetch/authorize is a silent
                # no-op here, never a crash — same tolerance
                # `list_interface_objects` already shows for a registry
                # that can outgrow what one call site hardcodes.
                if source_name is None or target_name is None:
                    continue

                col = await storage_column(relation["source_object_type_urn"], relation["source_property"])
                if col is None:
                    continue

                if current_type == source_name:
                    fk_value = current_row.get(col)
                    if fk_value is None:
                        continue
                    neighbor_type = target_name
                    if neighbor_type not in authorized_types:
                        await core._authorize_object_type(principal, core.OBJECT_TYPE_URNS[neighbor_type], "read")
                        authorized_types.add(neighbor_type)
                    neighbor_row = await core._resolve_one(
                        neighbor_type, principal.tenant_id, fk_value,
                        core.FETCH_FNS[neighbor_type], core.ID_KWARGS[neighbor_type], principal=principal,
                    )
                    neighbor_rows = [neighbor_row] if neighbor_row is not None else []
                    direction = "toward_one"
                elif current_type == target_name:
                    neighbor_type = source_name
                    if neighbor_type not in authorized_types:
                        await core._authorize_object_type(principal, core.OBJECT_TYPE_URNS[neighbor_type], "read")
                        authorized_types.add(neighbor_type)
                    neighbor_rows = await core._resolve_many(
                        neighbor_type, principal.tenant_id, core.FETCH_FNS[neighbor_type], principal=principal,
                        filter_column=col, filter_kwarg=col, filter_value=current_id,
                    )
                    direction = "toward_many"
                else:
                    continue

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
    if object_type not in core.OBJECT_TYPE_URNS:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType {object_type!r}")
    await core._authorize_object_type(principal, core.OBJECT_TYPE_URNS[object_type], "read")

    typed_id = _coerce_instance_id(object_type, instance_id)
    root_row = await core._resolve_one(
        object_type, principal.tenant_id, typed_id,
        core.FETCH_FNS[object_type], core.ID_KWARGS[object_type], principal=principal,
    )
    if root_row is None:
        raise HTTPException(status_code=404, detail=f"{object_type}/{instance_id} not found")

    return await _traverse_neighborhood(object_type, root_row["id"], root_row, hops, principal)
