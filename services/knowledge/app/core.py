"""Authorization and process-wide clients for the Knowledge service.

Instance reads, confidential masking, and derived properties live in
``reads``, ``masking``, and ``derived``. Their names stay on this module.
``pool`` and ``authz`` are assigned here by lifespan; other modules must
use ``core.pool`` and ``core.authz`` so they observe that assignment.
"""

from __future__ import annotations

import functools
import logging
import os
from typing import Optional

from fastapi import Header, Query

from holon_common import HolonError, Principal, active_jwt, build_urn, make_principal_dependency, require_urn_tenant_match
from holon_common.iceberg_env import iceberg_catalog_config_from_env
from holon_common.iceberg_env import iceberg_kwargs as _iceberg_kwargs
from holon_common.spicedb_id import spicedb_object_id

from . import function_registry as function_registry
from . import ontology, resolver
from . import serving_store as serving_store
from .derived import (
    _ALLOWED_AGGREGATES as _ALLOWED_AGGREGATES,
    _DEFAULT_COLLECT_LIMIT as _DEFAULT_COLLECT_LIMIT,
    _MAX_LINK_AGGREGATE_HOPS as _MAX_LINK_AGGREGATE_HOPS,
    _apply_derived_properties as _apply_derived_properties,
    _compute_link_aggregate as _compute_link_aggregate,
    _compute_struct_reducer as _compute_struct_reducer,
    _link_aggregate_path as _link_aggregate_path,
    _reduce_array as _reduce_array,
    _skip_derived_property as _skip_derived_property,
)
from .masking import (
    _coerce_property_types as _coerce_property_types,
    _filter_by_instance_markings as _filter_by_instance_markings,
    _mask_and_derive as _mask_and_derive,
    _mask_confidential_properties as _mask_confidential_properties,
    _parse_struct_or_array as _parse_struct_or_array,
)
from .reads import (
    _find_relation_by_link_name as _find_relation_by_link_name,
    _fk_filtered_fetch as _fk_filtered_fetch,
    _resolve_join_dataset_neighbors as _resolve_join_dataset_neighbors,
    _resolve_many as _resolve_many,
    _resolve_many_by_ids as _resolve_many_by_ids,
    _resolve_object_backed_neighbors as _resolve_object_backed_neighbors,
    _resolve_one as _resolve_one,
    _resolve_relation_neighbors as _resolve_relation_neighbors,
    instance_not_found as instance_not_found,
)


logger = logging.getLogger("knowledge")

TENANT_ID = os.environ["HOLON_TENANT_ID"]
WORKSPACE_ID = os.environ["HOLON_WORKSPACE_ID"]
JWT_SECRET, JWT_ACTIVE_KID, JWT_SECRETS = active_jwt()

# Instance reads are serving-store only. Iceberg is the warehouse
# (catalog ingest → `serving_store.materialize`). A miss is a miss —
# never a live scan marked `degraded: true`. Production posture still
# requires HOLON_SERVING_STORE_REQUIRE_MATERIALIZED so the operator
# flag matches this code path.

ICEBERG_CONFIG = iceberg_catalog_config_from_env()


def iceberg_kwargs(tenant_id: str) -> dict:
    return _iceberg_kwargs(tenant_id, config=ICEBERG_CONFIG)

current_principal = make_principal_dependency(JWT_SECRET, secrets=JWT_SECRETS)


async def current_workspace(
    workspace_id: Optional[str] = Query(None, alias="workspaceId"),
    x_holon_workspace_id: Optional[str] = Header(None, alias="X-Holon-Workspace-Id"),
) -> str:
    """Resolve the target workspace ID from query params, header, or default."""
    return workspace_id or x_holon_workspace_id or WORKSPACE_ID


class _NotReady:
    """Stand-in until lifespan assigns the real pool or authz client.

    Attribute access raises a 503 instead of AttributeError on None.
    """

    def __init__(self, what: str) -> None:
        self._what = what

    def __getattr__(self, name: str):
        raise HolonError.unavailable(
            "KnowledgeNotReady",
            f"knowledge {self._what} is not ready",
        )


def require_pool():
    if isinstance(pool, _NotReady):
        raise HolonError.unavailable("KnowledgeNotReady", "knowledge pool is not ready")
    return pool


def require_authz():
    if isinstance(authz, _NotReady):
        raise HolonError.unavailable("KnowledgeNotReady", "knowledge authz is not ready")
    return authz


# Replaced by `main.py`'s lifespan() before the process serves traffic.
pool = _NotReady("pool")
authz = _NotReady("authz")
producer = None


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
