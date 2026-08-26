"""Value Types and type-classes."""

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


class ValueTypeRequest(BaseModel):
    name: str
    base_type: str
    format_regex: Optional[str] = None
    constraints: Optional[list[dict]] = None
    description: str = ""
    api_name: Optional[str] = None
    display_name: str = ""
    example_value: Optional[str] = None
    lifecycle_status: str = "experimental"
    format_regex_match: str = "full"
    project_urn: Optional[str] = None
    deprecation_reason: Optional[str] = None
    deprecation_deadline: Optional[str] = None
    replacement_urn: Optional[str] = None


@router.post("/value-types", status_code=201)
async def create_value_type(request: ValueTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    """Registering a Value Type is ontology governance, same tier as
    registering an Interface or a Marking — the workspace's own
    `approve` permission. Seeds SpiceDB `parent_workspace` (and optional
    `parent_project` for Foundry-style project import).
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_value_type(core.pool, principal.tenant_id, request.name) is not None:
        raise HolonError.conflict('ValueTypeAlreadyExists', f"value type already exists: {request.name}")
    project_urn = await _validate_optional_project_urn(request.project_urn)
    try:
        created = await ontology.create_value_type(
            core.pool,
            tenant_id=principal.tenant_id,
            name=request.name,
            base_type=request.base_type,
            format_regex=request.format_regex,
            constraints=request.constraints,
            description=request.description,
            api_name=request.api_name,
            display_name=request.display_name,
            example_value=request.example_value,
            lifecycle_status=request.lifecycle_status,
            format_regex_match=request.format_regex_match,
            project_urn=project_urn,
            deprecation_reason=request.deprecation_reason,
            deprecation_deadline=request.deprecation_deadline,
            replacement_urn=request.replacement_urn,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('ValueTypeValidationFailed', str(exc)) from exc
    await _seed_value_type_authz(
        tenant_id=principal.tenant_id,
        workspace_id=workspace_id,
        urn=created["urn"],
        name=created["name"],
    )
    try:
        await _link_value_type_to_project(created["urn"], created.get("project_urn"))
    except Exception as exc:
        raise HolonError.unavailable('Unavailable', f"Value Type created but SpiceDB parent_project reconcile failed: {exc}",) from exc
    return created


@router.get("/value-types")
async def list_value_types(
    principal: Principal = Depends(core.current_principal),
    include_deprecated: bool = True,
) -> list[dict]:
    return await ontology.list_value_types(
        core.pool, principal.tenant_id, include_deprecated=include_deprecated
    )


@router.get("/type-classes")
async def list_known_type_classes(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """Catalog of Foundry-shaped type classes Holon understands (UI suggestions)."""
    from ...ontology.type_classes import KNOWN_TYPE_CLASSES

    return [
        {"id": key, "kind": key.split(":", 1)[0], "name": key.split(":", 1)[1], **meta}
        for key, meta in sorted(KNOWN_TYPE_CLASSES.items())
    ]


class ValidateValueTypeCastsRequest(BaseModel):
    """Pipeline Builder logical-type cast: map output columns → Value Type names."""

    casts: dict[str, str]
    rows: list[dict]


@router.post("/value-types/validate-casts")
async def validate_value_type_casts(
    request: ValidateValueTypeCastsRequest, principal: Principal = Depends(core.current_principal)
) -> dict:
    """Validate row values against named Value Types (Connectivity pipeline casts).

    Authenticated-only like `/functions/{name}/invoke` — no workspace gate;
    cast rules reference registry Value Types, not workspace-scoped data.
    """
    if not request.casts:
        raise HolonError.invalid_argument('InvalidCasts', "casts must be a non-empty column → value_type map")
    errors: list[dict] = []
    for index, row in enumerate(request.rows):
        if not isinstance(row, dict):
            errors.append({"row_index": index, "column": "*", "detail": "row must be an object"})
            continue
        errors.extend(
            await ontology.validate_value_type_casts(
                core.pool,
                principal.tenant_id,
                casts=request.casts,
                row=row,
                row_index=index,
            )
        )
    return {"ok": not errors, "error_count": len(errors), "errors": errors[:50]}


@router.get("/value-types/{name}")
async def get_value_type(name: str, principal: Principal = Depends(core.current_principal)) -> dict:
    value_type = await ontology.get_value_type(core.pool, principal.tenant_id, name)
    if value_type is None:
        raise HolonError.not_found('ValueTypeNotFound', f"unknown value type: {name}")
    await _authorize_value_type(principal, value_type["urn"], "read")
    return value_type


@router.get("/value-types/{name}/revisions")
async def get_value_type_revisions(name: str, principal: Principal = Depends(core.current_principal)) -> list[dict]:
    value_type = await ontology.get_value_type(core.pool, principal.tenant_id, name)
    if value_type is None:
        raise HolonError.not_found('ValueTypeNotFound', f"unknown value type: {name}")
    await _authorize_value_type(principal, value_type["urn"], "read")
    return await ontology.list_value_type_revisions(core.pool, principal.tenant_id, name)


@router.get("/value-types/{name}/permissions")
async def get_value_type_permissions(
    name: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """Foundry Permissions tab — effective ReBAC on the Value Type URN."""
    value_type = await ontology.get_value_type(core.pool, principal.tenant_id, name)
    if value_type is None:
        raise HolonError.not_found('ValueTypeNotFound', f"unknown value type: {name}")
    urn = value_type["urn"]
    await _authorize_value_type(principal, urn, "read")
    parent_workspace_urn = ontology.workspace_urn(principal.tenant_id, workspace_id)
    tiers = ("read", "write", "approve")
    decisions = await asyncio.gather(
        *(
            core.authz.authorize(principal, resource_type="value_type", resource_urn=urn, permission=permission)
            for permission in tiers
        )
    )
    permissions = {permission: decision.allowed for permission, decision in zip(tiers, decisions)}
    return {
        "name": name,
        "urn": urn,
        "parent_workspace_urn": parent_workspace_urn,
        "project_urn": value_type.get("project_urn"),
        "permissions": permissions,
    }


class UpdateValueTypeRequest(BaseModel):
    format_regex: Optional[str] = None
    constraints: Optional[list[dict]] = None
    description: Optional[str] = None
    api_name: Optional[str] = None
    display_name: Optional[str] = None
    example_value: Optional[str] = None
    clear_example_value: bool = False
    lifecycle_status: Optional[str] = None
    format_regex_match: Optional[str] = None
    project_urn: Optional[str] = None
    clear_project_urn: bool = False
    deprecation_reason: Optional[str] = None
    deprecation_deadline: Optional[str] = None
    replacement_urn: Optional[str] = None


@router.put("/value-types/{name}")
async def update_value_type(
    name: str, request: UpdateValueTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """Per-URN write (SpiceDB) after create seeded parent_workspace.
    Changing constraints/format_regex/format_regex_match bumps `version`.
    """
    current = await ontology.get_value_type(core.pool, principal.tenant_id, name)
    if current is None:
        raise HolonError.not_found('ValueTypeNotFound', f"unknown value type: {name}")
    await _authorize_value_type(principal, current["urn"], "write")
    project_urn = None
    if not request.clear_project_urn and request.project_urn is not None:
        project_urn = await _validate_optional_project_urn(request.project_urn)
    try:
        updated = await ontology.update_value_type(
            core.pool,
            tenant_id=principal.tenant_id,
            name=name,
            format_regex=request.format_regex,
            constraints=request.constraints,
            description=request.description,
            api_name=request.api_name,
            display_name=request.display_name,
            example_value=request.example_value,
            clear_example_value=request.clear_example_value,
            lifecycle_status=request.lifecycle_status,
            format_regex_match=request.format_regex_match,
            project_urn=project_urn,
            clear_project_urn=request.clear_project_urn,
            deprecation_reason=request.deprecation_reason,
            deprecation_deadline=request.deprecation_deadline,
            replacement_urn=request.replacement_urn,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('ValueTypeValidationFailed', str(exc)) from exc
    if request.clear_project_urn or request.project_urn is not None:
        try:
            await _link_value_type_to_project(updated["urn"], updated.get("project_urn"))
        except Exception as exc:
            raise HolonError.unavailable('Unavailable', f"Value Type updated but SpiceDB parent_project reconcile failed: {exc}",) from exc
    return updated


class DeprecateValueTypeRequest(BaseModel):
    deprecation_reason: str
    deprecation_deadline: str
    replacement_urn: Optional[str] = None


@router.post("/value-types/{name}/deprecate")
async def deprecate_value_type(
    name: str,
    request: DeprecateValueTypeRequest,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """Foundry-style deprecate — prefer over delete when consumers exist."""
    current = await ontology.get_value_type(core.pool, principal.tenant_id, name)
    if current is None:
        raise HolonError.not_found('ValueTypeNotFound', f"unknown value type: {name}")
    await _authorize_value_type(principal, current["urn"], "approve")
    try:
        return await ontology.deprecate_value_type(
            core.pool,
            tenant_id=principal.tenant_id,
            name=name,
            deprecation_reason=request.deprecation_reason,
            deprecation_deadline=request.deprecation_deadline,
            replacement_urn=request.replacement_urn,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('ValueTypeValidationFailed', str(exc)) from exc
