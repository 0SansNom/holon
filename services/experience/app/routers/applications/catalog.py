"""Application drafts, project link, and promote."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from holon_common import HolonError, Principal
from holon_common.audit import emit_audit

from ... import application_builder, deps
from ...deps import (
    INTELLIGENCE_URL,
    KNOWLEDGE_URL,
    WORKSPACE_ID,
    _authorize_application,
    _get_application_or_404,
    _link_application_to_project,
    _upstream_authorization,
    current_principal,
)

router = APIRouter()

class ApplicationDefinitionRequest(BaseModel):
    definition: dict[str, Any]


@router.post("/api/applications/{name}")
async def create_or_update_application(
    name: str,
    body: ApplicationDefinitionRequest,
    http_request: Request,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Idempotent on an unpromoted draft (edits it in
    place); creates a new draft version if the latest is promoted.
    """
    urn = application_builder.application_urn(principal.tenant_id, WORKSPACE_ID, name)
    existing = await application_builder.get_application(deps.pool, tenant_id=principal.tenant_id, name=name)
    if existing is not None:
        await _authorize_application(principal, urn, "write")
    else:
        decision = await deps.authz.authorize(
            principal, resource_type="workspace", resource_urn=deps.WORKSPACE_URN, permission="write",
        )
        if not decision.allowed:
            raise HolonError.forbidden("PermissionDenied", decision.reason)

    authorization = _upstream_authorization(http_request) or ""
    try:
        result = await application_builder.create_or_update_draft(
            deps.pool,
            deps.client,
            tenant_id=principal.tenant_id,
            workspace_id=WORKSPACE_ID,
            name=name,
            definition=body.definition,
            knowledge_url=KNOWLEDGE_URL,
            intelligence_url=INTELLIGENCE_URL,
            authorization=authorization,
        )
    except application_builder.InvalidApplicationDefinition as exc:
        raise HolonError.invalid_argument('ExperienceValidationFailed', str(exc)) from exc

    if existing is None:
        await deps.authz.write_relationship(
            resource_type="application", resource_urn=urn, relation="parent_workspace",
            subject_type="workspace", subject_urn=deps.WORKSPACE_URN,
        )
    emit_audit(
        category="access",
        action="experience.application.saved",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="application",
        resource_urn=urn,
        extra={"created": existing is None, "name": name},
    )
    return result


@router.get("/api/applications")
async def list_applications(principal: Principal = Depends(current_principal)) -> list[dict]:
    """A real, previously-missing gap — every prior verification already
    knew the Application's name from creating it. Returns the latest
    version of each distinct application for this tenant.

    Filtered post-fetch rather than per-row-authorized: `authorize()`'s own
    decision cache makes repeat checks for the same principal+permission
    cheap, and the list is small at this build's scale (unlike a paged
    object table, where per-row authz would actually matter).
    """
    applications = await application_builder.list_applications(deps.pool, tenant_id=principal.tenant_id)
    allowed = []
    for application in applications:
        decision = await deps.authz.authorize(
            principal, resource_type="application", resource_urn=application["urn"], permission="read",
        )
        if decision.allowed:
            allowed.append(application)
    return allowed


@router.get("/api/applications/{name}")
async def get_application(name: str, principal: Principal = Depends(current_principal)) -> dict:
    application = await application_builder.get_application(deps.pool, tenant_id=principal.tenant_id, name=name)
    if application is None:
        raise deps._application_not_found(name)
    await _authorize_application(principal, application["urn"], "read")
    return application


class SetApplicationProjectRequest(BaseModel):
    project_urn: Optional[str] = None


@router.post("/api/applications/{name}/project")
async def set_application_project(
    name: str, body: SetApplicationProjectRequest, principal: Principal = Depends(current_principal),
) -> dict:
    application = await application_builder.get_application(deps.pool, tenant_id=principal.tenant_id, name=name)
    if application is None:
        raise deps._application_not_found(name)
    # Same bar as any other edit to this Application — no separate
    # project-level check, mirroring how `knowledge`'s ObjectType project
    # scoping is gated by the object_type's own `write`, not the target
    # project's (the resource owner's call to make, not the project's to
    # approve).
    await _authorize_application(principal, application["urn"], "write")
    await _link_application_to_project(application["urn"], body.project_urn)
    return await application_builder.set_application_project(
        deps.pool, tenant_id=principal.tenant_id, name=name, project_urn=body.project_urn,
    )


@router.post("/api/applications/{name}/promote")
async def promote_application(
    name: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> dict:
    await _get_application_or_404(name, principal, permission="write")
    authorization = _upstream_authorization(http_request) or ""
    try:
        result = await application_builder.promote(
            deps.pool,
            deps.client,
            tenant_id=principal.tenant_id,
            name=name,
            knowledge_url=KNOWLEDGE_URL,
            intelligence_url=INTELLIGENCE_URL,
            authorization=authorization,
        )
    except application_builder.InvalidApplicationDefinition as exc:
        raise HolonError.invalid_argument('ExperienceValidationFailed', str(exc)) from exc
    emit_audit(
        category="access",
        action="experience.application.promoted",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="application",
        resource_urn=application_builder.application_urn(principal.tenant_id, WORKSPACE_ID, name),
        extra={"name": name},
    )
    return result
