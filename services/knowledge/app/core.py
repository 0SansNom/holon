"""Shared data access and authorization core for Knowledge service.

Provides object resolution, authorization checks, masking, and derived property evaluation.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
from datetime import datetime
from typing import Any, Optional

from fastapi import Header, Query

from holon_common import HolonError, Principal, active_jwt, build_urn, make_principal_dependency, require_urn_tenant_match
from holon_common.spicedb_id import spicedb_object_id

from . import function_registry, ontology, resolver, serving_store
from .struct_values import assemble_struct_value, parse_struct_or_array
from pyiceberg.exceptions import NoSuchTableError

logger = logging.getLogger("knowledge")

TENANT_ID = os.environ["HOLON_TENANT_ID"]
WORKSPACE_ID = os.environ["HOLON_WORKSPACE_ID"]
JWT_SECRET, JWT_ACTIVE_KID, JWT_SECRETS = active_jwt()

# Instance reads are serving-store only. Iceberg is the warehouse
# (catalog ingest → `serving_store.materialize`). A miss is a miss —
# never a live scan marked `degraded: true`. Production posture still
# requires HOLON_SERVING_STORE_REQUIRE_MATERIALIZED so the operator
# flag matches this code path.

ICEBERG_CONFIG = dict(
    catalog_uri=os.environ["HOLON_ICEBERG_CATALOG_URI"],
    warehouse=os.environ["HOLON_ICEBERG_WAREHOUSE"],
    s3_endpoint=os.environ["HOLON_S3_ENDPOINT"],
    access_key=os.environ["AWS_ACCESS_KEY_ID"],
    secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    region=os.environ["AWS_REGION"],
)


def iceberg_kwargs(tenant_id: str) -> dict:
    return {**ICEBERG_CONFIG, "tenant_id": tenant_id}

current_principal = make_principal_dependency(JWT_SECRET, secrets=JWT_SECRETS)


async def current_workspace(
    workspace_id: Optional[str] = Query(None, alias="workspaceId"),
    x_holon_workspace_id: Optional[str] = Header(None, alias="X-Holon-Workspace-Id"),
) -> str:
    """Resolve the target workspace ID from query params, header, or default."""
    return workspace_id or x_holon_workspace_id or WORKSPACE_ID


# Set once by `main.py`'s `lifespan()` at startup — see module docstring.
pool = None
authz = None


async def _object_type_urn_for(object_type: str, tenant_id: str = TENANT_ID, workspace_id: str = WORKSPACE_ID) -> str:
    """Resolve ObjectType URN for this tenant/workspace, raising `KeyError`
    if no such ObjectType is catalogued.
    """
    urn = ontology.object_type_urn(tenant_id, workspace_id, object_type)
    row = await ontology.get_object_type(pool, urn)
    if row is None:
        raise KeyError(object_type)
    return urn


async def _type_handle(object_type: str, tenant_id: str = TENANT_ID, workspace_id: str = WORKSPACE_ID) -> Optional[dict]:
    """Resolve any ObjectType (seeded or self-serve) to a fetch handle —
    ontology row → `functools.partial(resolver.fetch_generic, dataset)`
    with `id_kwarg="id_value"`. Built on `_object_type_urn_for`. Returns
    `None` for an unknown name rather than raising, since neighbor
    traversal call sites already treat a missing type as a skip.
    """
    try:
        urn = await _object_type_urn_for(object_type, tenant_id, workspace_id)
    except KeyError:
        return None
    definition = await ontology.get_object_type(pool, urn)
    if definition is None:
        return None
    dataset_name = definition["source_dataset_urn"].rsplit(":", 1)[-1]
    return {"urn": urn, "fetch_fn": functools.partial(resolver.fetch_generic, dataset_name), "id_kwarg": "id_value"}


def _fk_filtered_fetch(fetch_fn, filter_column: str):
    """Adapts a `fetch_generic` partial (which only understands
    `filter_column`/`filter_value`) to `_resolve_many`'s calling
    convention — `fetch_fn(**{filter_kwarg: filter_value}, **iceberg_config)`.
    """
    def _call(**kwargs):
        filter_value = kwargs.pop(filter_column, None)
        return fetch_fn(filter_column=filter_column, filter_value=filter_value, **kwargs)
    return _call


async def _is_authorized_read(principal: Principal, object_type_urn: str) -> bool:
    """`_authorize_object_type` for a *neighbor* type reached mid-traversal —
    a 403 (ReBAC/ABAC/marking denial, or a cross-tenant URN) means "this
    branch doesn't exist for this principal", the same "omit, don't abort"
    treatment `_resolve_one` already gives a denied/missing instance one
    level down. A 500 (miscatalogued ObjectType — a real server bug, not
    an access decision) is deliberately not swallowed here and still
    propagates, matching `_authorize_object_type`'s own "fail loudly rather
    than guess a classification" for that case.
    """
    try:
        await _authorize_object_type(principal, object_type_urn, "read")
        return True
    except HolonError as exc:
        if exc.status_code == 403:
            return False
        raise


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
        source_definition = await ontology.get_object_type(pool, relation["source_object_type_urn"])
        if source_definition is None:
            return None
        mapping = source_definition["property_mapping"]
        property_mapping_cache[relation["source_object_type_urn"]] = mapping
    col = mapping.get(relation["source_property"])
    if col is None:
        return None

    if current_type == source_name:
        neighbor_type = target_name
        handle = await _type_handle(neighbor_type, principal.tenant_id)
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
            if not await _is_authorized_read(principal, handle["urn"]):
                return None
            authorized_types.add(neighbor_type)
        neighbor_row = await _resolve_one(
            neighbor_type, principal.tenant_id, fk_value, handle["fetch_fn"], handle["id_kwarg"], principal=principal,
        )
        return neighbor_type, ([neighbor_row] if neighbor_row is not None else []), "toward_one"

    neighbor_type = source_name
    handle = await _type_handle(neighbor_type, principal.tenant_id)
    if handle is None:
        return None
    if neighbor_type not in authorized_types:
        if not await _is_authorized_read(principal, handle["urn"]):
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
    from . import link_overlays, resolver

    join_urn = relation.get("join_dataset_urn")
    src_col = relation.get("join_source_column")
    tgt_col = relation.get("join_target_column")
    if not join_urn or not src_col or not tgt_col:
        return None
    dataset_name = join_urn.rsplit(":", 1)[-1]
    join_rows: list[dict] = []
    try:
        join_rows = await asyncio.to_thread(resolver.fetch_generic, dataset_name, **iceberg_kwargs(principal.tenant_id))
    except NoSuchTableError:
        join_rows = []

    base_pairs = [
        (r.get(src_col), r.get(tgt_col))
        for r in join_rows
        if r.get(src_col) is not None and r.get(tgt_col) is not None
    ]
    overlays = await link_overlays.list_overlays(
        pool, tenant_id=principal.tenant_id, relation_urn=relation["urn"]
    )
    pairs = link_overlays.merge_pair_set(base_pairs, overlays)

    if current_type == source_name:
        neighbor_type = target_name
        neighbor_ids = [t for s, t in pairs if s == str(current_id)]
        direction = "toward_many"
    else:
        neighbor_type = source_name
        neighbor_ids = [s for s, t in pairs if t == str(current_id)]
        direction = "toward_many"

    handle = await _type_handle(neighbor_type, principal.tenant_id)
    if handle is None:
        return None
    if neighbor_type not in authorized_types:
        if not await _is_authorized_read(principal, handle["urn"]):
            return None
        authorized_types.add(neighbor_type)
    neighbors: list[dict] = []
    for nid in neighbor_ids:
        try:
            coerced = int(nid) if isinstance(nid, str) and nid.isdigit() else nid
        except (TypeError, ValueError):
            coerced = nid
        row = await _resolve_one(
            neighbor_type, principal.tenant_id, coerced, handle["fetch_fn"], handle["id_kwarg"], principal=principal,
        )
        if row is not None:
            neighbors.append(row)
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
    mid_def = await ontology.get_object_type(pool, mid_urn)
    if mid_def is None:
        return None
    mid_mapping = mid_def["property_mapping"]
    property_mapping_cache[mid_urn] = mid_mapping
    src_col = mid_mapping.get(mid_src_prop)
    tgt_col = mid_mapping.get(mid_tgt_prop)
    if not src_col or not tgt_col:
        return None

    mid_handle = await _type_handle(mid_name, principal.tenant_id)
    if mid_handle is None:
        return None
    if mid_name not in authorized_types:
        if not await _is_authorized_read(principal, mid_handle["urn"]):
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
    overlays = await link_overlays.list_overlays(
        pool, tenant_id=principal.tenant_id, relation_urn=relation["urn"]
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
    handle = await _type_handle(neighbor_type, principal.tenant_id)
    if handle is None:
        return None
    if neighbor_type not in authorized_types:
        if not await _is_authorized_read(principal, handle["urn"]):
            return None
        authorized_types.add(neighbor_type)
    neighbors: list[dict] = []
    for nid in neighbor_ids:
        try:
            coerced = int(nid) if isinstance(nid, str) and str(nid).isdigit() else nid
        except (TypeError, ValueError):
            coerced = nid
        row = await _resolve_one(
            neighbor_type, principal.tenant_id, coerced, handle["fetch_fn"], handle["id_kwarg"], principal=principal,
        )
        if row is not None:
            matching_mids = [m for m in mid_rows if str(m.get(project_col)) == str(nid)]
            if matching_mids:
                row = {**row, "_link_object": matching_mids[0], "_link_object_type": mid_name}
            neighbors.append(row)
    return neighbor_type, neighbors, "toward_many"


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


allowed_countries: set = set()
producer = None


async def held_marking_names(principal: Principal) -> list[str]:
    """Marking names the principal `hold`s. Used as search entitlement
    tokens so instance-level markings filter at the index (R8.6), not
    after the hit list.
    """
    markings = await ontology.list_markings(pool, principal.tenant_id)
    if not markings:
        return []
    try:
        held_ids = await authz.lookup_resource_ids(
            resource_type="marking", permission="hold", principal_urn=principal.urn
        )
        if principal.on_behalf_of:
            mandant_ids = await authz.lookup_resource_ids(
                resource_type="marking", permission="hold", principal_urn=principal.on_behalf_of
            )
            held_ids &= mandant_ids
        return [
            row["name"]
            for row in markings
            if spicedb_object_id(build_urn(principal.tenant_id, "global", "marking", row["name"])) in held_ids
        ]
    except Exception:
        logger.exception("marking LookupResources failed; falling back to per-marking CheckPermission")
        held: list[str] = []
        for row in markings:
            marking_urn = build_urn(principal.tenant_id, "global", "marking", row["name"])
            if await authz.check_rebac(principal.urn, "marking", marking_urn, "hold"):
                if principal.on_behalf_of and not await authz.check_rebac(
                    principal.on_behalf_of, "marking", marking_urn, "hold"
                ):
                    continue
                held.append(row["name"])
        return held


async def readable_object_type_names(principal: Principal) -> list[str]:
    """ObjectType names the principal may read (ReBAC ∩ markings).

    Search applies this as an OpenSearch `object_type` filter so a
    workspace-wide `/search` no longer requires workspace `read` — a
    project-only contractor sees their types and nothing else (R8.6).
    """
    types = await ontology.list_object_types(pool, principal.tenant_id)
    if not types:
        return []
    try:
        readable_ids = await authz.lookup_resource_ids(
            resource_type="object_type", permission="read", principal_urn=principal.urn
        )
        if principal.on_behalf_of:
            mandant_ids = await authz.lookup_resource_ids(
                resource_type="object_type", permission="read", principal_urn=principal.on_behalf_of
            )
            readable_ids &= mandant_ids
        candidates = [ot for ot in types if spicedb_object_id(ot["urn"]) in readable_ids]
    except Exception:
        logger.exception("object_type LookupResources failed; falling back to per-type CheckPermission")
        candidates = []
        for ot in types:
            if await authz.check_rebac(principal.urn, "object_type", ot["urn"], "read"):
                if principal.on_behalf_of and not await authz.check_rebac(
                    principal.on_behalf_of, "object_type", ot["urn"], "read"
                ):
                    continue
                candidates.append(ot)

    allowed: list[str] = []
    for ot in candidates:
        markings = ot.get("markings") or []
        if markings and not await _authorize_markings(principal, markings):
            continue
        allowed.append(ot["name"])
    return allowed


async def _authorize_object_type(principal: Principal, object_type_urn: str, permission: str) -> None:
    """Shared by every object-type endpoint. Hard tenant fence first
    (URN tenant must equal principal.tenant_id — ADR 026), then ReBAC/ABAC.
    """
    require_urn_tenant_match(principal, object_type_urn)

    object_type = await ontology.get_object_type(pool, object_type_urn)
    if object_type is None:
        # Callers resolve the URN (`_object_type_urn_for`/`_type_handle`) and
        # already turn "unknown ObjectType" into a 404 before ever reaching
        # here — so a miss at this point means the catalogue changed under
        # us between that check and this one, a real server-side race, not
        # a merely-undefined resource. Fail loudly rather than guess a
        # classification.
        raise HolonError.internal(
            "ObjectTypeNotCatalogued",
            f"ObjectType {object_type_urn} is not catalogued",
            object_type_urn=object_type_urn,
        )

    resource_attributes = {} if permission == "read" else {"classification": object_type["classification"]}
    decision = await authz.authorize(
        principal,
        resource_type="object_type",
        resource_urn=object_type_urn,
        permission=permission,
        resource_attributes=resource_attributes,
    )
    if not decision.allowed:
        raise HolonError.forbidden(
            "PermissionDenied",
            decision.reason,
            resource_urn=object_type_urn,
            permission=permission,
        )

    markings = object_type.get("markings") or []
    if markings and not await _authorize_markings(principal, markings):
        raise HolonError.forbidden(
            "MarkingDenied",
            f"missing required marking(s) on {object_type_urn}: {markings}",
            object_type_urn=object_type_urn,
            markings=markings,
        )


async def _authorize_markings(principal: Principal, markings: list[str]) -> bool:
    """Markings on top of ReBAC/ABAC: evaluate per category.

    CONJUNCTIVE categories require every applied marking held;
    DISJUNCTIVE require at least one. Categories AND together. SpiceDB
    `marking` stays flat (`hold = holder + admin`). Unknown registry
    names fail closed. Bypasses `authorize()`'s decision cache (keyed for
    object_type/permission, not marking lists).
    """
    if not markings:
        return True
    meta = await ontology.marking_authz_meta(pool, principal.tenant_id, markings)
    if len(meta) != len(set(markings)):
        return False
    held: dict[str, bool] = {}
    for name in {m["name"] for m in meta}:
        marking_urn = build_urn(principal.tenant_id, "global", "marking", name)
        held[name] = await authz.check_rebac(principal.urn, "marking", marking_urn, "hold")
    return ontology.category_groups_satisfied(meta, held)


async def _mask_confidential_properties(
    object_type_urn: str, principal: Principal, rows: list[dict], *, key_prefix: str = ""
) -> list[dict]:
    """Row/column security enforcement point. A confidential
    property is replaced with `None` (and named in `_maskedFields`) rather
    than the whole object being withheld; a principal whose country
    passes ABAC gets every field, unmasked. Uses the same OPA-sourced
    `allowed_countries` set `search.py` mirrors (fetched once at startup
    via `PermissionClient.get_policy_data`) — consistent single source of
    truth, not a second hand-copied policy.

    `key_prefix`: `execute_plan`'s `join` operation returns rows
    with every column prefixed `s_`/`t_` to keep same-named columns from
    two different ObjectTypes from colliding — this masks
    `{key_prefix}{confidential_column}` instead of the bare column name,
    called once per side with each side's own classifications, same
    function either way.
    """
    if principal.country in allowed_countries:
        return rows
    property_classifications = await ontology.get_property_classifications(pool, object_type_urn)
    confidential_properties = {name for name, classification in property_classifications.items() if classification == "confidential"}
    if not confidential_properties:
        return rows

    masked_rows = []
    for row in rows:
        row = dict(row)
        masked_fields = [
            f"{key_prefix}{name}" for name in confidential_properties if row.get(f"{key_prefix}{name}") is not None
        ]
        for name in masked_fields:
            row[name] = None
        if masked_fields:
            row.setdefault("_maskedFields", [])
            row["_maskedFields"] = row["_maskedFields"] + masked_fields
        masked_rows.append(row)
    return masked_rows


_ALLOWED_AGGREGATES = {"sum", "count", "avg", "min", "max", "collect_list", "collect_set"}
_MAX_LINK_AGGREGATE_HOPS = 3
_DEFAULT_COLLECT_LIMIT = 10


def _link_aggregate_path(rule: dict) -> list[str]:
    path = rule.get("path")
    return path if isinstance(path, list) else []


async def _compute_link_aggregate(
    rule: dict, object_type_name: str, row: dict, principal: Principal,
    *, relation_types: list[dict], authorized_types: set[str], property_mapping_cache: dict[str, dict],
    neighbor_property_mapping_cache: dict[str, dict],
) -> Optional[Any]:
    """A Foundry-style reducer over a 1–3 hop RelationType path:
    `count`/`sum`/`avg`/`min`/`max`/`collect_list`/`collect_set`.
    Reuses `_resolve_relation_neighbors` per hop — no separate fetch
    path. Returns `None` (property skipped) if the path, neighbor type,
    or aggregated property can't be resolved.
    """
    path = _link_aggregate_path(rule)
    if not path or len(path) > _MAX_LINK_AGGREGATE_HOPS or "id" not in row:
        return None

    frontier: list[tuple[str, dict]] = [(object_type_name, row)]
    for link_name in path:
        next_frontier: list[tuple[str, dict]] = []
        for current_type, current_row in frontier:
            relation = _find_relation_by_link_name(relation_types, current_type, link_name)
            if relation is None or "id" not in current_row:
                continue
            result = await _resolve_relation_neighbors(
                relation, current_type, current_row["id"], current_row, principal,
                authorized_types=authorized_types, property_mapping_cache=property_mapping_cache,
            )
            if result is None:
                continue
            neighbor_type, neighbor_rows, _direction = result
            for neighbor_row in neighbor_rows:
                next_frontier.append((neighbor_type, neighbor_row))
        frontier = next_frontier
        if not frontier:
            break

    neighbor_rows = [neighbor_row for _type, neighbor_row in frontier]
    aggregate = rule.get("aggregate")
    if aggregate == "count":
        return len(neighbor_rows)
    if not frontier:
        return None

    neighbor_type = frontier[0][0]
    neighbor_property = rule.get("property")
    neighbor_mapping = neighbor_property_mapping_cache.get(neighbor_type)
    if neighbor_mapping is None:
        neighbor_handle = await _type_handle(neighbor_type, principal.tenant_id)
        if neighbor_handle is None:
            return None
        neighbor_definition = await ontology.get_object_type(pool, neighbor_handle["urn"])
        if neighbor_definition is None:
            return None
        neighbor_mapping = neighbor_definition["property_mapping"]
        neighbor_property_mapping_cache[neighbor_type] = neighbor_mapping
    neighbor_column = neighbor_mapping.get(neighbor_property)
    if neighbor_column is None:
        return None

    raw_values = [neighbor_row.get(neighbor_column) for neighbor_row in neighbor_rows]
    values = [v for v in raw_values if v is not None]

    if aggregate in ("collect_list", "collect_set"):
        limit = rule.get("collect_limit", _DEFAULT_COLLECT_LIMIT)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            limit = _DEFAULT_COLLECT_LIMIT
        if aggregate == "collect_set":
            seen: set[str] = set()
            unique: list[Any] = []
            for v in values:
                key = json.dumps(v, sort_keys=True, default=str)
                if key in seen:
                    continue
                seen.add(key)
                unique.append(v)
                if len(unique) >= limit:
                    break
            return unique
        return values[:limit]

    numeric = [float(v) for v in values]
    if not numeric:
        return None
    if aggregate == "sum":
        return sum(numeric)
    if aggregate == "avg":
        return sum(numeric) / len(numeric)
    if aggregate == "min":
        return min(numeric)
    if aggregate == "max":
        return max(numeric)
    return None


def _reduce_array(values: list, reducer: str, by: Optional[str]) -> Optional[Any]:
    """The struct-reducer's actual reduction — `first`/`last` are
    positional; `latest`/`max` and `earliest`/`min` compare either the
    raw element (`by` is `None`, a scalar array) or one of its fields
    (`by` set, a struct array). A `TypeError` from comparing
    incompatible/missing values propagates to the caller, which already
    treats a failure here as "skip this property for this row", not a
    crash — same contract every other derived-property path already has.
    """
    if reducer == "first":
        return values[0]
    if reducer == "last":
        return values[-1]
    key = (lambda v: v.get(by)) if by else (lambda v: v)
    if reducer in ("latest", "max"):
        return max(values, key=key)
    if reducer in ("earliest", "min"):
        return min(values, key=key)
    return None


def _compute_struct_reducer(rule: dict, object_type: dict, row: dict) -> Optional[Any]:
    """Foundry's other real "derived property" reducer — this one over
    one of *this* ObjectType's own array properties (struct array or
    scalar array), rather than a linked type's. The array value is
    already a parsed Python list by the time this runs: `_mask_and_derive`
    always runs `_coerce_property_types` before `_apply_derived_properties`,
    so a `struct`/`array`-kind property's JSON text is already real
    nested data here, not a string to re-parse.
    """
    array_property = rule.get("property")
    column = (object_type.get("property_mapping") or {}).get(array_property)
    if column is None:
        return None
    array_value = row.get(column)
    if not isinstance(array_value, list) or not array_value:
        return None
    return _reduce_array(array_value, rule.get("reducer"), rule.get("by"))


async def _apply_derived_properties(object_type_urn: str, rows: list[dict], principal: Principal) -> list[dict]:
    """Read-time computation of every `derived_properties` entry — a
    plain string is a Function plugin invocation (the original,
    unchanged shape); a `{"kind": "link_aggregate", ...}` dict is a
    reducer over a RelationType (`_compute_link_aggregate`); a
    `{"kind": "struct_reducer", ...}` dict is a reducer over one of this
    ObjectType's own array properties (`_compute_struct_reducer`) —
    Foundry's other two real "derived property" mechanisms. All three
    translate their inputs to *ontology* property names via `property_mapping`
    (not the raw source-column keys `resolver.py`/`serving_store.py`
    return — an ontology-level concept shouldn't need to know storage
    column names, except `struct_reducer`, which reads its own array
    property's already-parsed value directly off the row).
    If a Function's required input was masked to `None` by
    `_mask_confidential_properties`, that derived property is skipped
    entirely rather than computed from a missing value — never a
    misleading default silently leaking a shape of the masked data.
    Plugin lookups happen once per declared derived property, not once
    per row; a `link_aggregate`'s RelationType registry is likewise
    fetched at most once per call, not once per row.
    """
    if not rows:
        return rows
    object_type = await ontology.get_object_type(pool, object_type_urn)
    derived = (object_type.get("derived_properties") or {}) if object_type else {}
    if not derived:
        return rows
    property_mapping = object_type["property_mapping"]
    object_type_name = object_type_urn.rsplit(":", 1)[-1]

    function_entries = {name: value for name, value in derived.items() if isinstance(value, str)}
    link_aggregate_entries = {
        name: value for name, value in derived.items() if isinstance(value, dict) and value.get("kind") == "link_aggregate"
    }
    struct_reducer_entries = {
        name: value for name, value in derived.items() if isinstance(value, dict) and value.get("kind") == "struct_reducer"
    }

    resolved: dict[str, tuple[dict, Any]] = {}
    for property_name, function_name in function_entries.items():
        registration = await function_registry.find_active_function_by_name(pool, function_name)
        if registration is not None:
            resolved[property_name] = (registration, function_registry.load_function_plugin(registration["manifest"]))

    relation_types = await ontology.list_relation_types(pool, principal.tenant_id) if link_aggregate_entries else []
    authorized_types = {object_type_name}
    property_mapping_cache: dict[str, dict] = {}
    neighbor_property_mapping_cache: dict[str, dict] = {}

    result_rows = []
    for row in rows:
        row = dict(row)
        translated = {camel: row.get(source_col) for camel, source_col in property_mapping.items()}
        for property_name, (registration, plugin) in resolved.items():
            required = (registration["manifest"].get("input_schema") or {}).get("required", [])
            if any(translated.get(field) is None for field in required):
                continue
            try:
                output = await plugin.call(**translated)
            except Exception:
                # Function plugins performing external I/O (such as HTTP calls to
                # Intelligence) may fail due to downstream outages or model errors.
                # Isolate plugin execution errors so a single failed derived property
                # leaves that property absent rather than failing the entire read.
                logger.exception(
                    "derived property %r (function %r) failed for %s, skipping it for this row",
                    property_name, registration["manifest"].get("function_name"), object_type_urn,
                )
                continue
            if isinstance(output, dict) and property_name in output:
                row[property_name] = output[property_name]
        for property_name, rule in link_aggregate_entries.items():
            try:
                value = await _compute_link_aggregate(
                    rule, object_type_name, row, principal,
                    relation_types=relation_types, authorized_types=authorized_types,
                    property_mapping_cache=property_mapping_cache,
                    neighbor_property_mapping_cache=neighbor_property_mapping_cache,
                )
            except Exception:
                logger.exception(
                    "derived property %r (link_aggregate over %r) failed for %s, skipping it for this row",
                    property_name, rule.get("path"), object_type_urn,
                )
                continue
            if value is not None:
                row[property_name] = value
        for property_name, rule in struct_reducer_entries.items():
            try:
                value = _compute_struct_reducer(rule, object_type, row)
            except Exception:
                logger.exception(
                    "derived property %r (struct_reducer over %r) failed for %s, skipping it for this row",
                    property_name, rule.get("property"), object_type_urn,
                )
                continue
            if value is not None:
                row[property_name] = value
        result_rows.append(row)
    return result_rows


def _parse_struct_or_array(property_name: str, rule: dict, raw_value: Any) -> Any:
    """Compatibility wrapper — see ``struct_values.parse_struct_or_array``."""
    return parse_struct_or_array(rule, raw_value)


async def _coerce_property_types(object_type_urn: str, rows: list[dict]) -> list[dict]:
    """Read-time struct/array parsing for `property_types` (see
    `0000_baseline.sql` / `object_type.property_types`) — operates on the row's
    *raw* source-column keys, the same keys `_resolve_one`/`_resolve_many`
    already return unchanged for every ordinary property (property_mapping
    is a declared/checked contract, not a runtime rename — see
    `_apply_derived_properties`'s own docstring for why that translation
    only ever happens internally, just for a Function's inputs).
    """
    if not rows:
        return rows
    object_type = await ontology.get_object_type(pool, object_type_urn)
    property_types = (object_type.get("property_types") or {}) if object_type else {}
    structured = {name: rule for name, rule in property_types.items() if rule.get("kind") in ("struct", "array")}
    if not structured:
        return rows
    property_mapping = object_type["property_mapping"]

    result_rows = []
    for row in rows:
        row = dict(row)
        for property_name, rule in structured.items():
            source_col = property_mapping.get(property_name)
            if rule.get("kind") == "struct":
                assembled = assemble_struct_value(rule, row, source_col)
                if assembled is not None and source_col is not None:
                    row[source_col] = assembled
                elif assembled is not None and source_col is None:
                    row[property_name] = assembled
                continue
            if source_col is None or source_col not in row:
                continue
            row[source_col] = _parse_struct_or_array(property_name, rule, row[source_col])
        result_rows.append(row)
    return result_rows


async def _mask_and_derive(object_type_urn: str, principal: Principal, rows: list[dict]) -> list[dict]:
    """The combined read choke point: property masking, then struct/array
    type coercion, then derived properties — masking first since a
    derived property must never see a value the principal itself
    couldn't see unmasked; coercion before derivation so a Function
    declared against a structured property receives the real parsed
    shape, not a raw JSON string.
    """
    masked = await _mask_confidential_properties(object_type_urn, principal, rows)
    coerced = await _coerce_property_types(object_type_urn, masked)
    return await _apply_derived_properties(object_type_urn, coerced, principal)


async def _filter_by_instance_markings(
    object_type_urn: str, tenant_id: str, principal: Principal, rows: list[dict]
) -> list[dict]:
    """Instance-level markings — the other attachment point
    alongside ObjectType-wide `markings` (already enforced earlier, in
    `_authorize_object_type`, before any row is even fetched). A row
    carrying an instance marking the principal doesn't hold is dropped
    entirely rather than masked — a marking is a coarse clearance gate,
    not a per-property one, so "denied" means the row doesn't exist for
    this principal, the same treatment ReBAC-denied resources already
    get elsewhere in this build (a filtered-out row surfaces as an empty
    list entry or, via `_resolve_one`, a 404 — never a 403 buried inside
    a 200 list).
    """
    if not rows:
        return rows
    instance_ids = [str(row["id"]) for row in rows]
    markings_by_instance = await ontology.get_instance_markings_bulk(
        pool, object_type_urn=object_type_urn, tenant_id=tenant_id, instance_ids=instance_ids
    )
    if not markings_by_instance:
        return rows
    result = []
    for row in rows:
        instance_markings = markings_by_instance.get(str(row["id"]))
        if instance_markings and not await _authorize_markings(principal, instance_markings):
            continue
        result.append(row)
    return result


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
    object_type_urn = await _object_type_urn_for(object_type, tenant_id)

    if as_of is not None:
        row = await serving_store.get_instance_as_of(pool, object_type, tenant_id, instance_id, as_of)
        if row is None:
            return None
        filtered = await _filter_by_instance_markings(object_type_urn, tenant_id, principal, [row])
        if not filtered:
            return None
        return (await _mask_and_derive(object_type_urn, principal, filtered))[0]

    data = await serving_store.get_instance(pool, object_type, tenant_id, instance_id)
    if data is not None:
        filtered = await _filter_by_instance_markings(object_type_urn, tenant_id, principal, [data])
        if not filtered:
            return None
        return (await _mask_and_derive(object_type_urn, principal, filtered))[0]
    if await serving_store.is_tombstoned(pool, object_type, tenant_id, instance_id):
        return None
    return None


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
    object_type_urn = await _object_type_urn_for(object_type, tenant_id)

    probe = await serving_store.list_instances(
        pool, object_type, tenant_id, filter_column=filter_column, filter_value=filter_value, limit=1
    )
    if probe or limit is None:
        if limit is None and after_id is None:
            rows = await serving_store.list_instances(
                pool, object_type, tenant_id, filter_column=filter_column, filter_value=filter_value
            )
            if rows:
                rows = await _filter_by_instance_markings(object_type_urn, tenant_id, principal, rows)
                return await _mask_and_derive(object_type_urn, principal, rows)
        elif probe or limit is not None:
            collected: list[dict] = []
            cursor = after_id
            for _ in range(32):
                need = (limit - len(collected)) if limit is not None else None
                batch_limit = None if need is None else max(need * 3, need, 16)
                batch = await serving_store.list_instances(
                    pool,
                    object_type,
                    tenant_id,
                    filter_column=filter_column,
                    filter_value=filter_value,
                    after_id=cursor,
                    limit=batch_limit,
                )
                if not batch:
                    break
                filtered = await _filter_by_instance_markings(object_type_urn, tenant_id, principal, batch)
                masked = await _mask_and_derive(object_type_urn, principal, filtered)
                collected.extend(masked)
                cursor = str(batch[-1].get("id"))
                if limit is not None and len(collected) >= limit:
                    return collected[:limit]
                if batch_limit is not None and len(batch) < batch_limit:
                    break
            if collected or probe:
                return collected if limit is None else collected[:limit]

    return []
