"""Marking categories, markings, grants, and instance markings."""

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


class MarkingCategoryRequest(BaseModel):
    name: str
    description: str = ""
    category_type: str = "CONJUNCTIVE"
    marking_type: str = "MANDATORY"


class MarkingRequest(BaseModel):
    name: str
    description: str = ""
    category_id: Optional[str] = None


@router.post("/marking-categories", status_code=201)
async def create_marking_category(
    request: MarkingCategoryRequest,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_marking_category(core.pool, principal.tenant_id, request.name) is not None:
        raise HolonError.conflict(
            "MarkingCategoryAlreadyExists", f"marking category already exists: {request.name}"
        )
    try:
        return await ontology.create_marking_category(
            core.pool,
            tenant_id=principal.tenant_id,
            name=request.name,
            description=request.description,
            category_type=request.category_type,
            marking_type=request.marking_type,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument("MarkingCategoryValidationFailed", str(exc)) from exc


@router.get("/marking-categories")
async def list_marking_categories(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    return await ontology.list_marking_categories(core.pool, principal.tenant_id)


@router.get("/marking-categories/{category_ref}")
async def get_marking_category(
    category_ref: str, principal: Principal = Depends(core.current_principal)
) -> dict:
    category = await ontology.get_marking_category(core.pool, principal.tenant_id, category_ref)
    if category is None:
        raise HolonError.not_found("MarkingCategoryNotFound", f"unknown marking category: {category_ref}")
    return category


@router.post("/markings", status_code=201)
async def create_marking(
    request: MarkingRequest,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """Register a Marking (label registry entry). Creator becomes SpiceDB
    `admin` on the marking; clearance grants are separate.
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_marking(core.pool, principal.tenant_id, request.name) is not None:
        raise HolonError.conflict("MarkingAlreadyExists", f"marking already exists: {request.name}")
    try:
        marking = await ontology.create_marking(
            core.pool,
            tenant_id=principal.tenant_id,
            name=request.name,
            description=request.description,
            category_id=request.category_id,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument("MarkingValidationFailed", str(exc)) from exc
    marking_urn = build_urn(principal.tenant_id, "global", "marking", marking["name"])
    await core.authz.write_relationship(
        resource_type="marking",
        resource_urn=marking_urn,
        relation="admin",
        subject_urn=principal.urn,
    )
    return marking


@router.get("/markings")
async def list_markings(
    principal: Principal = Depends(core.current_principal),
    category_id: Optional[str] = Query(None),
) -> list[dict]:
    return await ontology.list_markings(core.pool, principal.tenant_id, category_id=category_id)


@router.get("/markings/{marking_ref}")
async def get_marking(marking_ref: str, principal: Principal = Depends(core.current_principal)) -> dict:
    marking = await ontology.get_marking(core.pool, principal.tenant_id, marking_ref)
    if marking is None:
        raise HolonError.not_found("MarkingNotFound", f"unknown marking: {marking_ref}")
    return marking


@router.post("/markings/{marking_ref}/principals/{principal_urn:path}/access/grant")
async def grant_marking_access(
    marking_ref: str,
    principal_urn: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """Grants `holder` on `marking:{name}` — clearance `_authorize_markings`
    checks at read time. Allowed for marking admins or workspace approve.
    """
    marking = await ontology.get_marking(core.pool, principal.tenant_id, marking_ref)
    if marking is None:
        raise HolonError.not_found("MarkingNotFound", f"unknown marking: {marking_ref}")
    await _authorize_marking_administer(principal, workspace_id, marking["name"])
    marking_urn = build_urn(principal.tenant_id, "global", "marking", marking["name"])
    await core.authz.write_relationship(
        resource_type="marking",
        resource_urn=marking_urn,
        relation="holder",
        subject_urn=principal_urn,
    )
    return {"status": "granted", "principalUrn": principal_urn, "marking": marking["name"]}


@router.post("/markings/{marking_ref}/principals/{principal_urn:path}/access/revoke")
async def revoke_marking_access(
    marking_ref: str,
    principal_urn: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    marking = await ontology.get_marking(core.pool, principal.tenant_id, marking_ref)
    if marking is None:
        raise HolonError.not_found("MarkingNotFound", f"unknown marking: {marking_ref}")
    await _authorize_marking_administer(principal, workspace_id, marking["name"])
    marking_urn = build_urn(principal.tenant_id, "global", "marking", marking["name"])
    await core.authz.delete_relationship(
        resource_type="marking",
        resource_urn=marking_urn,
        relation="holder",
        subject_urn=principal_urn,
    )
    return {"status": "revoked", "principalUrn": principal_urn, "marking": marking["name"]}


class SetInstanceMarkingsRequest(BaseModel):
    markings: list[str]


@router.post("/objects/{object_type}/{instance_id}/markings")
async def set_instance_markings(
    object_type: str, instance_id: str, request: SetInstanceMarkingsRequest,
    principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """The other attachment point (alongside ObjectType-wide `markings`
    above): labeling one specific instance. Write-tier gated like any
    other mutation on this ObjectType — `_authorize_object_type` already
    also enforces any *ObjectType-wide* markings as part of that same
    check, so a principal locked out at the type level can't route
    around it by trying to label an individual instance either.
    """
    try:
        object_type_urn = await core._object_type_urn_for(object_type, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HolonError.not_found('ObjectTypeNotFound', f"unknown ObjectType: {object_type}")
    await core._authorize_object_type(principal, object_type_urn, "write")
    try:
        markings = await ontology.set_instance_markings(
            core.pool,
            object_type_urn=object_type_urn,
            tenant_id=principal.tenant_id,
            instance_id=instance_id,
            markings=request.markings,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('MarkingValidationFailed', str(exc)) from exc
    return {"objectType": object_type, "instanceId": instance_id, "markings": markings}
