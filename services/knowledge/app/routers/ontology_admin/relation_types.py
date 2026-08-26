"""Relation Types / Foundry Link Types."""

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


class RelationTypeRequest(BaseModel):
    name: str
    source_object_type: str
    target_object_type: str
    source_property: str = ""
    target_property: str
    cardinality: str
    storage_kind: str = "foreign_key"
    join_dataset_urn: Optional[str] = None
    join_source_column: Optional[str] = None
    join_target_column: Optional[str] = None
    mid_object_type: Optional[str] = None
    mid_source_property: Optional[str] = None
    mid_target_property: Optional[str] = None
    source_display_name: str = ""
    source_plural_display_name: str = ""
    source_api_name: Optional[str] = None
    source_visibility: str = "normal"
    target_display_name: str = ""
    target_plural_display_name: str = ""
    target_api_name: Optional[str] = None
    target_visibility: str = "normal"
    lifecycle_status: str = "experimental"
    type_classes: Optional[list[str]] = None
    project_urn: Optional[str] = None
    deprecation_reason: Optional[str] = None
    deprecation_deadline: Optional[str] = None
    replacement_urn: Optional[str] = None


@router.get("/relation-types")
async def list_relation_types(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """Same auth-only convention as `/ontology/{name}` — metadata, not
    instance data, so nothing for the PDP to check per-row.
    """
    return await ontology.list_relation_types(core.pool, principal.tenant_id)


class UpdateRelationTypeRequest(BaseModel):
    target_property: Optional[str] = None
    cardinality: Optional[str] = None
    storage_kind: Optional[str] = None
    join_dataset_urn: Optional[str] = None
    join_source_column: Optional[str] = None
    join_target_column: Optional[str] = None
    mid_object_type: Optional[str] = None
    mid_source_property: Optional[str] = None
    mid_target_property: Optional[str] = None
    source_display_name: Optional[str] = None
    source_plural_display_name: Optional[str] = None
    source_api_name: Optional[str] = None
    source_visibility: Optional[str] = None
    target_display_name: Optional[str] = None
    target_plural_display_name: Optional[str] = None
    target_api_name: Optional[str] = None
    target_visibility: Optional[str] = None
    lifecycle_status: Optional[str] = None
    type_classes: Optional[list[str]] = None
    project_urn: Optional[str] = None
    clear_project_urn: bool = False
    deprecation_reason: Optional[str] = None
    deprecation_deadline: Optional[str] = None
    replacement_urn: Optional[str] = None


@router.put("/relation-types/{name}")
async def update_relation_type(
    name: str, request: UpdateRelationTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """Source/target ObjectType and `source_property` aren't accepted
    here — they're the structural identity of the link. See
    `ontology/relation_types.py`'s `update_relation_type` docstring.
    """
    urn = ontology.relation_type_urn(principal.tenant_id, workspace_id, name)
    current = await ontology.get_relation_type(core.pool, urn)
    if current is None:
        raise HolonError.not_found('RelationTypeNotFound', f"unknown RelationType: {name}")
    await _authorize_relation_type(principal, urn, "write")
    project_urn = None
    if not request.clear_project_urn and request.project_urn is not None:
        project_urn = await _validate_optional_project_urn(request.project_urn)
    try:
        updated = await ontology.update_relation_type(
            core.pool,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            name=name,
            target_property=request.target_property,
            cardinality=request.cardinality,
            storage_kind=request.storage_kind,
            join_dataset_urn=request.join_dataset_urn,
            join_source_column=request.join_source_column,
            join_target_column=request.join_target_column,
            mid_object_type=request.mid_object_type,
            mid_source_property=request.mid_source_property,
            mid_target_property=request.mid_target_property,
            source_display_name=request.source_display_name,
            source_plural_display_name=request.source_plural_display_name,
            source_api_name=request.source_api_name,
            source_visibility=request.source_visibility,
            target_display_name=request.target_display_name,
            target_plural_display_name=request.target_plural_display_name,
            target_api_name=request.target_api_name,
            target_visibility=request.target_visibility,
            lifecycle_status=request.lifecycle_status,
            type_classes=request.type_classes,
            project_urn=project_urn,
            clear_project_urn=request.clear_project_urn,
            deprecation_reason=request.deprecation_reason,
            deprecation_deadline=request.deprecation_deadline,
            replacement_urn=request.replacement_urn,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('RelationTypeValidationFailed', str(exc)) from exc
    if request.clear_project_urn or request.project_urn is not None:
        try:
            await _link_relation_type_to_project(updated["urn"], updated.get("project_urn"))
        except Exception as exc:
            raise HolonError.unavailable('Unavailable', f"RelationType updated but SpiceDB parent_project reconcile failed: {exc}",) from exc
    return updated


@router.get("/relation-types/{name}")
async def get_relation_type(name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    urn = ontology.relation_type_urn(principal.tenant_id, workspace_id, name)
    relation_type = await ontology.get_relation_type(core.pool, urn)
    if relation_type is None:
        raise HolonError.not_found('RelationTypeNotFound', f"unknown RelationType: {name}")
    await _authorize_relation_type(principal, urn, "read")
    return relation_type


@router.get("/relation-types/{name}/permissions")
async def get_relation_type_permissions(
    name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    urn = ontology.relation_type_urn(principal.tenant_id, workspace_id, name)
    relation_type = await ontology.get_relation_type(core.pool, urn)
    if relation_type is None:
        raise HolonError.not_found('RelationTypeNotFound', f"unknown RelationType: {name}")
    await _authorize_relation_type(principal, urn, "read")
    parent_workspace_urn = ontology.workspace_urn(principal.tenant_id, workspace_id)
    tiers = ("read", "write", "approve")
    decisions = await asyncio.gather(
        *(
            core.authz.authorize(principal, resource_type="relation_type", resource_urn=urn, permission=permission)
            for permission in tiers
        )
    )
    permissions = {permission: decision.allowed for permission, decision in zip(tiers, decisions)}
    return {
        "name": name,
        "urn": urn,
        "parent_workspace_urn": parent_workspace_urn,
        "project_urn": relation_type.get("project_urn"),
        "permissions": permissions,
    }


@router.get("/relation-types/{name}/writeback-status")
async def get_relation_type_writeback_status(
    name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """Warn before changing storage/datasource when link overlays exist.

    Foundry surfaces Phonograph/writeback risks on Link Type edits; Holon
    reports overlay count + lifecycle so the OM UI can show a Callout.
    """
    from ... import link_overlays

    urn = ontology.relation_type_urn(principal.tenant_id, workspace_id, name)
    relation_type = await ontology.get_relation_type(core.pool, urn)
    if relation_type is None:
        raise HolonError.not_found('RelationTypeNotFound', f"unknown RelationType: {name}")
    await _authorize_relation_type(principal, urn, "read")
    overlay_count = await link_overlays.count_overlays(
        core.pool, tenant_id=principal.tenant_id, relation_urn=urn
    )
    lifecycle = relation_type.get("lifecycle_status") or "experimental"
    storage = relation_type.get("storage_kind") or "foreign_key"
    warnings: list[str] = []
    if lifecycle == "active":
        warnings.append("RelationType is active — storage/key changes may break existing apps and OSDK accessors.")
    if overlay_count > 0:
        warnings.append(
            f"{overlay_count} link overlay write(s) exist for this relation — changing join/mid storage "
            "will not migrate those overlays."
        )
    if storage == "foreign_key":
        warnings.append("FK link writes use object_instance_edit overlays, not Iceberg writeback.")
    return {
        "name": name,
        "urn": urn,
        "storage_kind": storage,
        "lifecycle_status": lifecycle,
        "overlay_count": overlay_count,
        "warnings": warnings,
        "has_writeback_risk": lifecycle == "active" or overlay_count > 0,
    }


@router.delete("/relation-types/{name}")
async def delete_relation_type(
    name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    urn = ontology.relation_type_urn(principal.tenant_id, workspace_id, name)
    current = await ontology.get_relation_type(core.pool, urn)
    if current is None:
        raise HolonError.not_found('RelationTypeNotFound', f"unknown RelationType: {name}")
    await _authorize_relation_type(principal, urn, "approve")
    try:
        await ontology.delete_relation_type(
            core.pool,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            name=name,
        )
    except ValueError as exc:
        detail = str(exc)
        status = 404 if detail.startswith("unknown") else 400
        raise HolonError.from_http(status, detail, error_name='RelationTypeValidationFailed') from exc
    try:
        await core.authz.delete_relationship(
            resource_type="relation_type",
            resource_urn=urn,
            relation="parent_workspace",
            subject_type="workspace",
            subject_urn=ontology.workspace_urn(principal.tenant_id, workspace_id),
        )
    except Exception:
        logger.exception("SpiceDB parent_workspace cleanup failed for deleted RelationType %s", urn)
    try:
        await _link_relation_type_to_project(urn, None)
    except Exception:
        logger.exception("SpiceDB parent_project cleanup failed for deleted RelationType %s", urn)
    return {"name": name, "deleted": True}


@router.post("/relation-types", status_code=201)
async def create_relation_type(request: RelationTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    """Registering a new RelationType is an ontology governance action, not
    a data read/write — gated on the workspace's own `approve` permission
    (admin-only, the same tier that decides high-risk Actions), not
    `_authorize_object_type` (there's no single ObjectType this belongs to;
    it connects two). Seeds SpiceDB `parent_workspace` (+ optional project).
    """
    decision = await core.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=ontology.workspace_urn(principal.tenant_id, workspace_id),
        permission="approve",
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)

    urn = ontology.relation_type_urn(principal.tenant_id, workspace_id, request.name)
    if await ontology.get_relation_type(core.pool, urn) is not None:
        raise HolonError.conflict('RelationTypeAlreadyExists', f"RelationType already exists: {request.name}")

    project_urn = await _validate_optional_project_urn(request.project_urn)
    try:
        created = await ontology.create_relation_type(
            core.pool,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            name=request.name,
            source_object_type=request.source_object_type,
            target_object_type=request.target_object_type,
            source_property=request.source_property,
            target_property=request.target_property,
            cardinality=request.cardinality,
            storage_kind=request.storage_kind,
            join_dataset_urn=request.join_dataset_urn,
            join_source_column=request.join_source_column,
            join_target_column=request.join_target_column,
            mid_object_type=request.mid_object_type,
            mid_source_property=request.mid_source_property,
            mid_target_property=request.mid_target_property,
            source_display_name=request.source_display_name,
            source_plural_display_name=request.source_plural_display_name,
            source_api_name=request.source_api_name,
            source_visibility=request.source_visibility,
            target_display_name=request.target_display_name,
            target_plural_display_name=request.target_plural_display_name,
            target_api_name=request.target_api_name,
            target_visibility=request.target_visibility,
            lifecycle_status=request.lifecycle_status,
            type_classes=request.type_classes,
            project_urn=project_urn,
            deprecation_reason=request.deprecation_reason,
            deprecation_deadline=request.deprecation_deadline,
            replacement_urn=request.replacement_urn,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('RelationTypeValidationFailed', str(exc)) from exc
    await _seed_relation_type_authz(
        tenant_id=principal.tenant_id,
        workspace_id=workspace_id,
        urn=created["urn"],
        name=created["name"],
    )
    try:
        await _link_relation_type_to_project(created["urn"], created.get("project_urn"))
    except Exception as exc:
        raise HolonError.unavailable('Unavailable', f"RelationType created but SpiceDB parent_project reconcile failed: {exc}",) from exc
    return created
