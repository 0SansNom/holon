"""ObjectType groups and object sets."""

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


class ObjectTypeGroupRequest(BaseModel):
    name: str
    description: str = ""
    object_types: list[str] = []


@router.post("/object-type-groups", status_code=201)
async def create_object_type_group(
    request: ObjectTypeGroupRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """A purely navigational registry — same governance tier as
    Interfaces/Markings/Value Types, not its own permission concept.
    """
    await _authorize_ontology_governance(principal, workspace_id)
    try:
        return await ontology.create_object_type_group(
            core.pool,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            name=request.name,
            description=request.description,
            object_types=request.object_types,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('ObjectTypeValidationFailed', str(exc)) from exc


@router.get("/object-type-groups")
async def list_object_type_groups(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    return await ontology.list_object_type_groups(core.pool, principal.tenant_id)


@router.put("/object-type-groups/{name}")
async def update_object_type_group(
    name: str,
    request: ObjectTypeGroupRequest,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    await _authorize_ontology_governance(principal, workspace_id)
    try:
        return await ontology.update_object_type_group(
            core.pool,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            name=name,
            description=request.description,
            object_types=request.object_types,
        )
    except ValueError as exc:
        detail = str(exc)
        status = 404 if detail.startswith("unknown ObjectType group") else 400
        raise HolonError.from_http(status, detail, error_name='ObjectTypeValidationFailed') from exc


@router.delete("/object-type-groups/{name}", status_code=204)
async def delete_object_type_group(
    name: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> Response:
    await _authorize_ontology_governance(principal, workspace_id)
    try:
        await ontology.delete_object_type_group(core.pool, principal.tenant_id, name)
    except ValueError as exc:
        raise HolonError.not_found("ObjectTypeGroupNotFound", str(exc)) from exc
    return Response(status_code=204)


class ObjectSetRequest(BaseModel):
    name: str
    object_type: str
    definition: dict
    display_name: str = ""
    description: str = ""
    lifecycle_status: str = "experimental"
    visibility: str = "normal"


class UpdateObjectSetRequest(BaseModel):
    definition: Optional[dict] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    lifecycle_status: Optional[str] = None
    visibility: Optional[str] = None


@router.get("/object-sets")
async def list_object_sets(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    return await ontology.list_object_sets(core.pool, principal.tenant_id)


@router.post("/object-sets", status_code=201)
async def create_object_set(request: ObjectSetRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    await _authorize_ontology_governance(principal, workspace_id)
    try:
        return await ontology.create_object_set(
            core.pool,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            name=request.name,
            object_type=request.object_type,
            definition=request.definition,
            display_name=request.display_name,
            description=request.description,
            lifecycle_status=request.lifecycle_status,
            visibility=request.visibility,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('ObjectSetValidationFailed', str(exc)) from exc


@router.get("/object-sets/{name}")
async def get_object_set(name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    urn = ontology.object_set_urn(principal.tenant_id, workspace_id, name)
    row = await ontology.get_object_set(core.pool, urn)
    if row is None:
        raise HolonError.not_found('ObjectSetNotFound', f"unknown object set: {name}")
    return row


@router.put("/object-sets/{name}")
async def update_object_set(
    name: str, request: UpdateObjectSetRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    await _authorize_ontology_governance(principal, workspace_id)
    try:
        return await ontology.update_object_set(
            core.pool,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            name=name,
            definition=request.definition,
            display_name=request.display_name,
            description=request.description,
            lifecycle_status=request.lifecycle_status,
            visibility=request.visibility,
        )
    except ValueError as exc:
        status = 404 if "unknown" in str(exc) else 400
        raise HolonError.from_http(
            status,
            str(exc),
            error_name="ObjectSetNotFound" if status == 404 else "ObjectSetValidationFailed",
        ) from exc


@router.get("/object-sets/{name}/objects")
async def evaluate_object_set(name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    """Evaluate the set filter against live instances — PDP-gated via `_resolve_many`."""
    urn = ontology.object_set_urn(principal.tenant_id, workspace_id, name)
    obj_set = await ontology.get_object_set(core.pool, urn)
    if obj_set is None:
        raise HolonError.not_found('ObjectSetNotFound', f"unknown object set: {name}")
    if obj_set.get("visibility") == "hidden":
        try:
            await _authorize_ontology_governance(principal, workspace_id)
        except HolonError:
            raise HolonError.not_found('ObjectSetNotFound', f"unknown object set: {name}")

    object_type = obj_set["object_type_urn"].rsplit(":", 1)[-1]
    handle = await core._type_handle(object_type, principal.tenant_id)
    if handle is None:
        raise HolonError.not_found('ObjectTypeNotFound', f"backing ObjectType {object_type!r} missing")
    await core._authorize_object_type(principal, handle["urn"], "read")
    ot = await ontology.get_object_type(core.pool, obj_set["object_type_urn"])
    rows = await core._resolve_many(
        object_type, principal.tenant_id, handle["fetch_fn"], principal=principal,
    )
    mapping = (ot or {}).get("property_mapping") or {}
    matched = [r for r in rows if ontology.matches_predicates(r, obj_set["definition"], mapping)]
    for row in matched:
        row["title"] = ontology.title_of(row, ot)
    return {"object_set": name, "object_type": object_type, "count": len(matched), "data": matched}
