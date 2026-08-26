"""Interface types and implementing-object listing."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from pyiceberg.exceptions import NoSuchTableError

from holon_common import EventActor, EventEnvelope, HolonError, Principal, build_urn

from ... import actions, catalog, glossary, ontology, ontology_health, query_log, resolver
from ... import core
from ...paging import interface_instance_key
from ..objects.paging_deps import page_response, paging_query
from ._auth import (
    IDENTITY_URL,
    _ALLOWED_CLASSIFICATIONS,
    _authorize_marking_administer,
    _authorize_ontology_governance,
    _authorize_ontology_write,
    _authorize_relation_type,
    _authorize_shared_property_type,
    _authorize_value_type,
    _authorize_workspace_read,
    _identity_validation_token,
    _link_object_type_to_project,
    _link_relation_type_to_project,
    _link_shared_property_type_to_project,
    _link_value_type_to_project,
    _seed_relation_type_authz,
    _seed_shared_property_type_authz,
    _seed_value_type_authz,
    _validate_optional_project_urn,
    _validate_resource_type,
)

router = APIRouter()
logger = logging.getLogger("knowledge.ontology_admin")


class InterfaceTypeRequest(BaseModel):
    name: str
    required_properties: list[str] = []
    required_actions: list[str] = []
    description: str = ""
    lifecycle_status: str = "experimental"
    deprecation_reason: Optional[str] = None
    deprecation_deadline: Optional[str] = None
    replacement_urn: Optional[str] = None
    property_types: Optional[dict[str, dict]] = None
    link_constraints: Optional[list[dict]] = None
    parent_interfaces: Optional[list[str]] = None


@router.post("/interfaces", status_code=201)
async def create_interface_type(request: InterfaceTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    """Registering an Interface is ontology governance, same tier as
    RelationType registration and ObjectType-version publication — the
    workspace's own `approve` permission, not a per-ObjectType check
    (an interface isn't owned by any single ObjectType).
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_interface_type(core.pool, principal.tenant_id, request.name) is not None:
        raise HolonError.conflict('InterfaceAlreadyExists', f"interface already exists: {request.name}")
    try:
        return await ontology.create_interface_type(
            core.pool,
            tenant_id=principal.tenant_id,
            name=request.name,
            required_properties=request.required_properties,
            required_actions=request.required_actions,
            description=request.description,
            lifecycle_status=request.lifecycle_status,
            deprecation_reason=request.deprecation_reason,
            deprecation_deadline=request.deprecation_deadline,
            replacement_urn=request.replacement_urn,
            property_types=request.property_types,
            link_constraints=request.link_constraints,
            parent_interfaces=request.parent_interfaces,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('InterfaceValidationFailed', str(exc)) from exc


@router.get("/interfaces")
async def list_interface_types(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """Same auth-only convention as `/ontology`/`/relation-types` —
    metadata about a definition, nothing for the PDP to check per-row.
    """
    return await ontology.list_interface_types(core.pool, principal.tenant_id)


@router.get("/interfaces/{name}")
async def get_interface_type(name: str, principal: Principal = Depends(core.current_principal)) -> dict:
    interface = await ontology.get_interface_type(core.pool, principal.tenant_id, name)
    if interface is None:
        raise HolonError.not_found('InterfaceNotFound', f"unknown interface: {name}")
    return interface


class UpdateInterfaceTypeRequest(BaseModel):
    required_properties: Optional[list[str]] = None
    required_actions: Optional[list[str]] = None
    description: Optional[str] = None
    lifecycle_status: Optional[str] = None
    deprecation_reason: Optional[str] = None
    deprecation_deadline: Optional[str] = None
    replacement_urn: Optional[str] = None
    property_types: Optional[dict[str, dict]] = None
    link_constraints: Optional[list[dict]] = None
    parent_interfaces: Optional[list[str]] = None


@router.put("/interfaces/{name}")
async def update_interface_type(
    name: str, request: UpdateInterfaceTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """`name` isn't accepted here — it's the key referenced from every
    ObjectType's `implements` list.
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_interface_type(core.pool, principal.tenant_id, name) is None:
        raise HolonError.not_found('InterfaceNotFound', f"unknown interface: {name}")
    try:
        return await ontology.update_interface_type(
            core.pool,
            tenant_id=principal.tenant_id,
            name=name,
            required_properties=request.required_properties,
            required_actions=request.required_actions,
            description=request.description,
            lifecycle_status=request.lifecycle_status,
            deprecation_reason=request.deprecation_reason,
            deprecation_deadline=request.deprecation_deadline,
            replacement_urn=request.replacement_urn,
            property_types=request.property_types,
            link_constraints=request.link_constraints,
            parent_interfaces=request.parent_interfaces,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('InterfaceValidationFailed', str(exc)) from exc


@router.delete("/interfaces/{name}")
async def delete_interface_type(
    name: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """Hard-delete. Blocked while active, while children extend it, or
    while any published ObjectType still implements it (directly or via
    a child interface).
    """
    await _authorize_ontology_governance(principal, workspace_id)
    try:
        deleted = await ontology.delete_interface_type(
            core.pool, tenant_id=principal.tenant_id, name=name,
        )
    except ValueError as exc:
        detail = str(exc)
        status = 404 if detail.startswith("unknown") else 400
        raise HolonError.from_http(status, detail, error_name='InterfaceValidationFailed') from exc
    return deleted


@router.get("/interfaces/{name}/objects")
async def list_interface_objects(
    name: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
    page: tuple[int, Optional[str]] = Depends(paging_query),
) -> dict:
    """Polymorphic read: every instance of every ObjectType whose
    currently *published* `implements` names this interface, tagged with
    which ObjectType it came from. Walks all catalogued ObjectTypes
    (seeded + self-serve) via `_type_handle` and reuses `_resolve_many`
    (serving-store-first, masking, live fallback). OT the principal cannot
    read are omitted, same "skip, don't 403 the whole list" posture as
    graph traversal.
    """
    page_size, cursor = page
    if await ontology.get_interface_type(core.pool, principal.tenant_id, name) is None:
        raise HolonError.not_found('InterfaceNotFound', f"unknown interface: {name}")
    results: list[dict] = []
    for object_type in await ontology.list_object_types(core.pool, principal.tenant_id):
        implements = object_type.get("implements") or []
        expanded = await ontology.expand_implements(core.pool, principal.tenant_id, implements)
        if name not in expanded:
            continue
        object_type_name = object_type["name"]
        handle = await core._type_handle(object_type_name, principal.tenant_id, workspace_id)
        if handle is None:
            continue
        if not await core._is_authorized_read(principal, handle["urn"]):
            continue
        rows = await core._resolve_many(
            object_type_name, principal.tenant_id, handle["fetch_fn"], principal=principal,
        )
        for row in rows:
            row["_objectType"] = object_type_name
        results.extend(rows)
    return page_response(results, page_size=page_size, cursor=cursor, key_of=interface_instance_key)
