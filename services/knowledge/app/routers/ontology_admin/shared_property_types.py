"""Shared Property Types."""

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


class SharedPropertyTypeRequest(BaseModel):
    api_name: str
    display_name: str
    value_type: Optional[str] = None
    struct_properties: Optional[dict[str, dict]] = None
    description: str = ""
    visibility: str = "normal"
    render_hints: Optional[list[str]] = None
    type_classes: Optional[list[str]] = None
    property_format: Optional[dict] = None
    aliases: Optional[list[str]] = None
    project_urn: Optional[str] = None


@router.post("/shared-property-types", status_code=201)
async def create_shared_property_type(
    request: SharedPropertyTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """Registering a Shared Property Type is ontology governance, same
    tier as registering a Value Type. Seeds SpiceDB `parent_workspace`
    (and optional `parent_project`) so later mutate checks are per-URN.
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_shared_property_type(core.pool, principal.tenant_id, request.api_name) is not None:
        raise HolonError.conflict('SharedPropertyTypeAlreadyExists', f"shared property type already exists: {request.api_name}")
    project_urn = await _validate_optional_project_urn(request.project_urn)
    try:
        created = await ontology.create_shared_property_type(
            core.pool,
            tenant_id=principal.tenant_id,
            api_name=request.api_name,
            display_name=request.display_name,
            value_type=request.value_type,
            struct_properties=request.struct_properties,
            description=request.description,
            visibility=request.visibility,
            render_hints=request.render_hints,
            type_classes=request.type_classes,
            property_format=request.property_format,
            aliases=request.aliases,
            project_urn=project_urn,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('SharedPropertyValidationFailed', str(exc)) from exc
    await _seed_shared_property_type_authz(
        tenant_id=principal.tenant_id,
        workspace_id=workspace_id,
        urn=created["urn"],
        api_name=created["api_name"],
    )
    try:
        await _link_shared_property_type_to_project(created["urn"], created.get("project_urn"))
    except Exception as exc:
        raise HolonError.unavailable('Unavailable', f"SPT created but SpiceDB parent_project reconcile failed: {exc}",) from exc
    return created


@router.get("/shared-property-types")
async def list_shared_property_types(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    return await ontology.list_shared_property_types(core.pool, principal.tenant_id)


@router.get("/shared-property-types/{api_name}")
async def get_shared_property_type(api_name: str, principal: Principal = Depends(core.current_principal)) -> dict:
    shared_property_type = await ontology.get_shared_property_type(core.pool, principal.tenant_id, api_name)
    if shared_property_type is None:
        raise HolonError.not_found('SharedPropertyTypeNotFound', f"unknown shared property type: {api_name}")
    await _authorize_shared_property_type(principal, shared_property_type["urn"], "read")
    return shared_property_type


@router.get("/shared-property-types/{api_name}/usage")
async def get_shared_property_type_usage(api_name: str, principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """Foundry Usage tab — ObjectTypes that reference this SPT."""
    shared_property_type = await ontology.get_shared_property_type(core.pool, principal.tenant_id, api_name)
    if shared_property_type is None:
        raise HolonError.not_found('SharedPropertyTypeNotFound', f"unknown shared property type: {api_name}")
    await _authorize_shared_property_type(principal, shared_property_type["urn"], "read")
    try:
        return await ontology.list_shared_property_type_usage(core.pool, principal.tenant_id, api_name)
    except ValueError as exc:
        raise HolonError.not_found('SharedPropertyTypeNotFound', str(exc)) from exc


@router.get("/shared-property-types/{api_name}/permissions")
async def get_shared_property_type_permissions(
    api_name: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """Foundry Permissions tab — effective ReBAC on the SPT URN."""
    shared_property_type = await ontology.get_shared_property_type(core.pool, principal.tenant_id, api_name)
    if shared_property_type is None:
        raise HolonError.not_found('SharedPropertyTypeNotFound', f"unknown shared property type: {api_name}")
    urn = shared_property_type["urn"]
    await _authorize_shared_property_type(principal, urn, "read")
    parent_workspace_urn = ontology.workspace_urn(principal.tenant_id, workspace_id)
    tiers = ("read", "write", "approve")
    decisions = await asyncio.gather(
        *(
            core.authz.authorize(
                principal, resource_type="shared_property_type", resource_urn=urn, permission=permission
            )
            for permission in tiers
        )
    )
    permissions = {permission: decision.allowed for permission, decision in zip(tiers, decisions)}
    return {
        "api_name": api_name,
        "urn": urn,
        "parent_workspace_urn": parent_workspace_urn,
        "project_urn": shared_property_type.get("project_urn"),
        "permissions": permissions,
    }


class UpdateSharedPropertyTypeRequest(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    visibility: Optional[str] = None
    render_hints: Optional[list[str]] = None
    type_classes: Optional[list[str]] = None
    property_format: Optional[dict] = None
    clear_property_format: bool = False
    aliases: Optional[list[str]] = None
    project_urn: Optional[str] = None
    clear_project_urn: bool = False


@router.put("/shared-property-types/{api_name}")
async def update_shared_property_type(
    api_name: str, request: UpdateSharedPropertyTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """Metadata-only update — authorized against the SPT URN (`write`)."""
    current = await ontology.get_shared_property_type(core.pool, principal.tenant_id, api_name)
    if current is None:
        raise HolonError.not_found('SharedPropertyTypeNotFound', f"unknown shared property type: {api_name}")
    await _authorize_shared_property_type(principal, current["urn"], "write")
    project_urn = None
    if not request.clear_project_urn and request.project_urn is not None:
        project_urn = await _validate_optional_project_urn(request.project_urn)
    try:
        updated = await ontology.update_shared_property_type(
            core.pool,
            tenant_id=principal.tenant_id,
            api_name=api_name,
            display_name=request.display_name,
            description=request.description,
            visibility=request.visibility,
            render_hints=request.render_hints,
            type_classes=request.type_classes,
            property_format=request.property_format,
            clear_property_format=request.clear_property_format,
            aliases=request.aliases,
            project_urn=project_urn,
            clear_project_urn=request.clear_project_urn,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('SharedPropertyValidationFailed', str(exc)) from exc
    if request.clear_project_urn or request.project_urn is not None:
        try:
            await _link_shared_property_type_to_project(updated["urn"], updated.get("project_urn"))
        except Exception as exc:
            raise HolonError.unavailable('Unavailable', f"SPT updated but SpiceDB parent_project reconcile failed: {exc}",) from exc
    return updated


@router.delete("/shared-property-types/{api_name}")
async def delete_shared_property_type(
    api_name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """Foundry parity: auto-detach then remove. Requires SPT `approve`."""
    current = await ontology.get_shared_property_type(core.pool, principal.tenant_id, api_name)
    if current is None:
        raise HolonError.not_found('SharedPropertyTypeNotFound', f"unknown shared property type: {api_name}")
    await _authorize_shared_property_type(principal, current["urn"], "approve")
    try:
        result = await ontology.delete_shared_property_type(core.pool, tenant_id=principal.tenant_id, api_name=api_name)
    except ValueError as exc:
        detail = str(exc)
        status = 404 if detail.startswith("unknown") else 400
        raise HolonError.from_http(status, detail, error_name='SharedPropertyValidationFailed') from exc
    try:
        await core.authz.delete_relationship(
            resource_type="shared_property_type",
            resource_urn=current["urn"],
            relation="parent_workspace",
            subject_type="workspace",
            subject_urn=ontology.workspace_urn(principal.tenant_id, workspace_id),
        )
    except Exception:
        logger.exception("SpiceDB parent_workspace cleanup failed for deleted SPT %s", current["urn"])
    try:
        await _link_shared_property_type_to_project(current["urn"], None)
    except Exception:
        logger.exception("SpiceDB parent_project cleanup failed for deleted SPT %s", current["urn"])
    return result
