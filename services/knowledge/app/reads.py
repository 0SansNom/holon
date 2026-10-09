"""Instance reads and relation-neighbor traversal."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from holon_common import HolonError, Principal

from . import core, ontology, serving_store


def _fk_filtered_fetch(fetch_fn, filter_column: str):
    """Adapts a `fetch_generic` partial (which only understands
    `filter_column`/`filter_value`) to `_resolve_many`'s calling
    convention — `fetch_fn(**{filter_kwarg: filter_value}, **iceberg_config)`.
    """
    def _call(**kwargs):
        filter_value = kwargs.pop(filter_column, None)
        return fetch_fn(filter_column=filter_column, filter_value=filter_value, **kwargs)
    return _call


async def _resolve_relation_neighbors(
    relation: dict, current_type: str, current_id, current_row: dict, principal: Principal,
    *, authorized_types: set[str], property_mapping_cache: dict[str, dict],
) -> Optional[tuple[str, list[dict], str]]:
    """Applies one RelationType to one instance, in whichever direction
    `current_type` sits on. Supports foreign_key, join_dataset (M:N), and
    object_backed storage kinds.
    """
    source_name = relation["source_object_type_urn"].rsplit(":", 1)[-1]
    target_name = relation["target_object_type_urn"].rsplit(":", 1)[-1]
    if current_type not in (source_name, target_name):
        return None

    storage = relation.get("storage_kind") or "foreign_key"

    if storage == "join_dataset":
        return await _resolve_join_dataset_neighbors(
            relation, current_type, current_id, principal,
            source_name=source_name, target_name=target_name,
            authorized_types=authorized_types,
        )

    if storage == "object_backed":
        return await _resolve_object_backed_neighbors(
            relation, current_type, current_id, principal,
            source_name=source_name, target_name=target_name,
            authorized_types=authorized_types,
            property_mapping_cache=property_mapping_cache,
        )

    mapping = property_mapping_cache.get(relation["source_object_type_urn"])
    if mapping is None:
        source_definition = await ontology.get_object_type(core.pool, relation["source_object_type_urn"])
        if source_definition is None:
            return None
        mapping = source_definition["property_mapping"]
        property_mapping_cache[relation["source_object_type_urn"]] = mapping
    col = mapping.get(relation["source_property"])
    if col is None:
        return None

    if current_type == source_name:
        neighbor_type = target_name
        handle = await core._type_handle(neighbor_type, principal.tenant_id)
        if handle is None:
            return None
        # Prefer ontology/API property overlays (Actions, link write) over the
        # raw Iceberg column when the overlay key is present — including an
        # explicit JSON null from unlink, which must clear the FK rather than
        # falling back to the source column.
        if relation["source_property"] in current_row:
            fk_value = current_row[relation["source_property"]]
        else:
            fk_value = current_row.get(col)
        if fk_value is None:
            return None
        if neighbor_type not in authorized_types:
            if not await core._is_authorized_read(principal, handle["urn"]):
                return None
            authorized_types.add(neighbor_type)
        neighbor_row = await _resolve_one(
            neighbor_type, principal.tenant_id, fk_value, handle["fetch_fn"], handle["id_kwarg"], principal=principal,
        )
        return neighbor_type, ([neighbor_row] if neighbor_row is not None else []), "toward_one"

    neighbor_type = source_name
    handle = await core._type_handle(neighbor_type, principal.tenant_id)
    if handle is None:
        return None
    if neighbor_type not in authorized_types:
        if not await core._is_authorized_read(principal, handle["urn"]):
            return None
        authorized_types.add(neighbor_type)
    fetch_fn = _fk_filtered_fetch(handle["fetch_fn"], col)
    neighbor_rows = await _resolve_many(
        neighbor_type, principal.tenant_id, fetch_fn, principal=principal,
        filter_column=col, filter_kwarg=col, filter_value=current_id,
    )
    return neighbor_type, neighbor_rows, "toward_many"


async def _resolve_join_dataset_neighbors(
    relation: dict, current_type: str, current_id, principal: Principal,
    *, source_name: str, target_name: str, authorized_types: set[str],
) -> Optional[tuple[str, list[dict], str]]:
    from . import link_overlays, relation_links

    join_urn = relation.get("join_dataset_urn")
    src_col = relation.get("join_source_column")
    tgt_col = relation.get("join_target_column")
    if not join_urn or not src_col or not tgt_col:
        return None

    as_source = current_type == source_name
    neighbor_type = target_name if as_source else source_name
    direction = "toward_many"
    from . import catalog

    await catalog.ensure_join_links_materialized(core.pool, relation, core.iceberg_kwargs(principal.tenant_id))
    base_ids, overlays = await asyncio.gather(
        relation_links.list_neighbor_ids(
            core.pool,
            tenant_id=principal.tenant_id,
            relation_urn=relation["urn"],
            current_id=current_id,
            as_source=as_source,
        ),
        link_overlays.list_overlays_for_instance(
            core.pool,
            tenant_id=principal.tenant_id,
            relation_urn=relation["urn"],
            current_id=current_id,
            as_source=as_source,
        ),
    )
    current = str(current_id)
    base_pairs = (
        [(current, nid) for nid in base_ids] if as_source else [(nid, current) for nid in base_ids]
    )
    pairs = link_overlays.merge_pair_set(base_pairs, overlays)
    neighbor_ids = [t for s, t in pairs if s == current] if as_source else [s for s, t in pairs if t == current]

    handle = await core._type_handle(neighbor_type, principal.tenant_id)
    if handle is None:
        return None
    if neighbor_type not in authorized_types:
        if not await core._is_authorized_read(principal, handle["urn"]):
            return None
        authorized_types.add(neighbor_type)
    neighbors = await _resolve_many_by_ids(
        neighbor_type, principal.tenant_id, neighbor_ids, principal=principal, object_type_urn=handle["urn"]
    )
    return neighbor_type, neighbors, direction


async def _resolve_object_backed_neighbors(
    relation: dict, current_type: str, current_id, principal: Principal,
    *, source_name: str, target_name: str, authorized_types: set[str],
    property_mapping_cache: dict[str, dict],
) -> Optional[tuple[str, list[dict], str]]:
    from . import link_overlays

    mid_urn = relation.get("mid_object_type_urn")
    mid_src_prop = relation.get("mid_source_property")
    mid_tgt_prop = relation.get("mid_target_property")
    if not mid_urn or not mid_src_prop or not mid_tgt_prop:
        return None
    mid_name = mid_urn.rsplit(":", 1)[-1]
    mid_def = await ontology.get_object_type(core.pool, mid_urn)
    if mid_def is None:
        return None
    mid_mapping = mid_def["property_mapping"]
    property_mapping_cache[mid_urn] = mid_mapping
    src_col = mid_mapping.get(mid_src_prop)
    tgt_col = mid_mapping.get(mid_tgt_prop)
    if not src_col or not tgt_col:
        return None

    mid_handle = await core._type_handle(mid_name, principal.tenant_id)
    if mid_handle is None:
        return None
    if mid_name not in authorized_types:
        if not await core._is_authorized_read(principal, mid_handle["urn"]):
            return None
        authorized_types.add(mid_name)

    if current_type == source_name:
        filter_col, project_col, neighbor_type = src_col, tgt_col, target_name
        filter_is_source = True
    else:
        filter_col, project_col, neighbor_type = tgt_col, src_col, source_name
        filter_is_source = False

    fetch_fn = _fk_filtered_fetch(mid_handle["fetch_fn"], filter_col)
    mid_rows = await _resolve_many(
        mid_name, principal.tenant_id, fetch_fn, principal=principal,
        filter_column=filter_col, filter_kwarg=filter_col, filter_value=current_id,
    )
    overlays = await link_overlays.list_overlays_for_instance(
        core.pool,
        tenant_id=principal.tenant_id,
        relation_urn=relation["urn"],
        current_id=current_id,
        as_source=filter_is_source,
    )
    mid_rows = link_overlays.filter_deleted_mids(mid_rows, overlays, src_col=src_col, tgt_col=tgt_col)
    mid_rows = mid_rows + link_overlays.overlay_mid_rows(
        overlays,
        src_col=src_col,
        tgt_col=tgt_col,
        current_id=current_id,
        filter_is_source=filter_is_source,
    )

    neighbor_ids = [r.get(project_col) for r in mid_rows if r.get(project_col) is not None]
    handle = await core._type_handle(neighbor_type, principal.tenant_id)
    if handle is None:
        return None
    if neighbor_type not in authorized_types:
        if not await core._is_authorized_read(principal, handle["urn"]):
            return None
        authorized_types.add(neighbor_type)
    neighbors = await _resolve_many_by_ids(
        neighbor_type, principal.tenant_id, neighbor_ids, principal=principal, object_type_urn=handle["urn"]
    )
    attached: list[dict] = []
    for row in neighbors:
        matching_mids = [m for m in mid_rows if str(m.get(project_col)) == str(row.get("id"))]
        if matching_mids:
            attached.append({**row, "_link_object": matching_mids[0], "_link_object_type": mid_name})
        else:
            attached.append(row)
    return neighbor_type, attached, "toward_many"


def _find_relation_by_link_name(relation_types: list[dict], object_type: str, link_name: str) -> Optional[dict]:
    """`link_name` matches a relation's forward or reverse accessor.

    Forward (this type is the source): `source_api_name` when set, else the
    local part of `name` (e.g. `Order.customer` → `customer`). Reverse (this
    type is the target): `target_api_name` when set, else `target_property`.
    Shared by `get_object_link` and `link_aggregate` — structural lookup only.
    """
    for relation in relation_types:
        source_name = relation["source_object_type_urn"].rsplit(":", 1)[-1]
        target_name = relation["target_object_type_urn"].rsplit(":", 1)[-1]
        local_name = relation["name"].split(".", 1)[-1]
        forward = (relation.get("source_api_name") or "").strip() or local_name
        reverse = (relation.get("target_api_name") or "").strip() or relation.get("target_property")
        if source_name == object_type and forward == link_name:
            return relation
        if target_name == object_type and reverse == link_name:
            return relation
        if source_name == object_type and local_name == link_name:
            return relation
        if target_name == object_type and relation.get("target_property") == link_name:
            return relation
    return None


async def _resolve_many_by_ids(
    object_type: str,
    tenant_id: str,
    instance_ids: list,
    *,
    principal: Principal,
    object_type_urn: str,
) -> list[dict]:
    rows = await serving_store.get_instances(core.pool, object_type, tenant_id, instance_ids)
    if not rows:
        return []
    filtered = await core._filter_by_instance_markings(object_type_urn, tenant_id, principal, rows)
    if not filtered:
        return []
    return await core._mask_and_derive(object_type_urn, principal, filtered)


async def _resolve_one(
    object_type: str,
    tenant_id: str,
    instance_id,
    fetch_fn,
    id_kwarg: str,
    *,
    principal: Principal,
    as_of: Optional[datetime] = None,
) -> Optional[dict]:
    """Serving-store read. A miss here means nothing has been
    materialized for this key yet — 404, not a live Iceberg scan.

    `as_of` historical read takes a different path entirely:
    a historical read either has recorded history to answer from or it
    doesn't.

    Property masking (and, after it, derived-property computation) is
    applied here unconditionally — this function (and `_resolve_many`
    below) is the single read choke point, so every one of the
    dozen-plus object-read endpoints gets it without a per-endpoint call.
    """
    object_type_urn = await core._object_type_urn_for(object_type, tenant_id)

    if as_of is not None:
        row = await serving_store.get_instance_as_of(core.pool, object_type, tenant_id, instance_id, as_of)
        if row is None:
            return None
        filtered = await core._filter_by_instance_markings(object_type_urn, tenant_id, principal, [row])
        if not filtered:
            return None
        return (await core._mask_and_derive(object_type_urn, principal, filtered))[0]

    data = await serving_store.get_instance(core.pool, object_type, tenant_id, instance_id)
    if data is not None:
        filtered = await core._filter_by_instance_markings(object_type_urn, tenant_id, principal, [data])
        if not filtered:
            return None
        return (await core._mask_and_derive(object_type_urn, principal, filtered))[0]
    if await serving_store.is_tombstoned(core.pool, object_type, tenant_id, instance_id):
        return None
    return None


async def instance_not_found(
    object_type: str, tenant_id: str, instance_id, *, as_of: Optional[datetime] = None
) -> HolonError:
    """The 404 for a `_resolve_one` miss. Called only after the caller passed
    the ObjectType read check, so saying the type has no data yet reveals
    nothing about the instance; a marking-denied instance stays a plain miss.
    """
    if as_of is None and not await serving_store.is_materialized(core.pool, object_type, tenant_id):
        return HolonError.not_found(
            "ObjectTypeNotMaterialized",
            f"{object_type} has not been materialized yet; instances become readable after its first sync",
            object_type=object_type,
            instance_id=instance_id,
        )
    detail = f"{object_type}/{instance_id} not found"
    if as_of is not None:
        detail += f" as of {as_of.isoformat()} (no history recorded yet at that time)"
    return HolonError.not_found("ObjectInstanceNotFound", detail, object_type=object_type, instance_id=instance_id)


async def _resolve_many(
    object_type: str,
    tenant_id: str,
    fetch_fn,
    *,
    principal: Principal,
    filter_column: Optional[str] = None,
    filter_kwarg: Optional[str] = None,
    filter_value=None,
    after_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict]:
    """Resolve instances, optionally keyset-paged at the serving store.

    When `limit` is set, over-fetches from Postgres until `limit` rows
    survive markings (or the store is exhausted). A serving-store miss
    is an empty list — Iceberg is not scanned for instance reads.
    """
    object_type_urn = await core._object_type_urn_for(object_type, tenant_id)

    probe = await serving_store.list_instances(
        core.pool, object_type, tenant_id, filter_column=filter_column, filter_value=filter_value, limit=1
    )
    if probe or limit is None:
        if limit is None and after_id is None:
            rows = await serving_store.list_instances(
                core.pool, object_type, tenant_id, filter_column=filter_column, filter_value=filter_value
            )
            if rows:
                rows = await core._filter_by_instance_markings(object_type_urn, tenant_id, principal, rows)
                return await core._mask_and_derive(object_type_urn, principal, rows)
        elif probe or limit is not None:
            collected: list[dict] = []
            cursor = after_id
            for _ in range(32):
                need = (limit - len(collected)) if limit is not None else None
                batch_limit = None if need is None else max(need * 3, need, 16)
                batch = await serving_store.list_instances(
                    core.pool,
                    object_type,
                    tenant_id,
                    filter_column=filter_column,
                    filter_value=filter_value,
                    after_id=cursor,
                    limit=batch_limit,
                )
                if not batch:
                    break
                filtered = await core._filter_by_instance_markings(object_type_urn, tenant_id, principal, batch)
                masked = await core._mask_and_derive(object_type_urn, principal, filtered)
                collected.extend(masked)
                cursor = str(batch[-1].get("id"))
                if limit is not None and len(collected) >= limit:
                    return collected[:limit]
                if batch_limit is not None and len(batch) < batch_limit:
                    break
            if collected or probe:
                return collected if limit is None else collected[:limit]

    return []
