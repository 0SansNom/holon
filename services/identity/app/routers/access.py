"""Workspace and project ReBAC grants."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from holon_common import HolonError, Principal
from holon_common.audit import emit_audit

from .. import deps
from ..deps import (
    AccessRequest,
    CreateProjectRequest,
    _access_listing,
    _authorize_project_governance,
    _enqueue_permission_event,
    _fanout_group_permission_event,
    _fetch_principal,
    _grant_subject_relation,
    _require_grant_target,
    _resolve_workspace_governance,
    _validate_project_relation,
    _validate_relation,
    current_principal,
)
from ..seed import (
    VALID_PROJECT_RELATIONS,
    VALID_WORKSPACE_RELATIONS,
    create_project,
    get_project,
    list_projects,
    project_urn,
    workspace_urn,
)


router = APIRouter()


@router.post("/principals/{principal_urn:path}/access/grant")
async def grant_access(
    principal_urn: str, request: AccessRequest, principal: Principal = Depends(current_principal)
) -> dict:
    _validate_relation(request.relation)
    target = await _fetch_principal(deps.pool, principal_urn)
    if target is None:
        raise HolonError.not_found('PrincipalNotFound', f"unknown principal: {principal_urn}")
    tid, wid = await _resolve_workspace_governance(
        principal, tenant_id=target.tenant_id, workspace_id=request.workspace_id
    )
    await _require_grant_target(principal_urn, tenant_id=tid)
    w_urn = workspace_urn(tid, wid)
    await deps.authz.write_relationship(
        resource_type="workspace",
        resource_urn=w_urn,
        relation=request.relation,
        subject_urn=principal_urn,
        optional_subject_relation=_grant_subject_relation(target),
    )
    await _enqueue_permission_event(
        event_type="identity.permission.granted",
        target_principal_urn=principal_urn,
        resource_type="workspace",
        resource_urn=w_urn,
        relation=request.relation,
        actor=principal,
        tenant_id=tid,
        workspace_id=wid,
    )
    if target.type == "group":
        await _fanout_group_permission_event(
            target,
            event_type="identity.permission.granted",
            resource_type="workspace",
            resource_urn=w_urn,
            relation=request.relation,
            actor=principal,
            tenant_id=tid,
            workspace_id=wid,
        )
    emit_audit(
        category="identity",
        action="identity.permission.granted",
        outcome="success",
        tenant_id=tid,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="workspace",
        resource_urn=w_urn,
        permission=request.relation,
        reason=f"granted {request.relation} to {principal_urn}",
        extra={"targetPrincipalUrn": principal_urn},
    )
    return {"status": "granted", "principalUrn": principal_urn, "relation": request.relation, "workspace_id": wid}



@router.post("/principals/{principal_urn:path}/access/revoke")
async def revoke_access(
    principal_urn: str, request: AccessRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Revoke workspace access for a principal."""
    _validate_relation(request.relation)
    target = await _fetch_principal(deps.pool, principal_urn)
    if target is None:
        raise HolonError.not_found('PrincipalNotFound', f"unknown principal: {principal_urn}")
    tid, wid = await _resolve_workspace_governance(
        principal, tenant_id=target.tenant_id, workspace_id=request.workspace_id
    )

    w_urn = workspace_urn(tid, wid)
    await deps.authz.delete_relationship(
        resource_type="workspace",
        resource_urn=w_urn,
        relation=request.relation,
        subject_urn=principal_urn,
        optional_subject_relation=_grant_subject_relation(target),
    )

    await _enqueue_permission_event(
        event_type="identity.permission.revoked",
        target_principal_urn=principal_urn,
        resource_type="workspace",
        resource_urn=w_urn,
        relation=request.relation,
        actor=principal,
        tenant_id=tid,
        workspace_id=wid,
    )
    if target.type == "group":
        await _fanout_group_permission_event(
            target,
            event_type="identity.permission.revoked",
            resource_type="workspace",
            resource_urn=w_urn,
            relation=request.relation,
            actor=principal,
            tenant_id=tid,
            workspace_id=wid,
        )

    emit_audit(
        category="identity",
        action="identity.permission.revoked",
        outcome="success",
        tenant_id=tid,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="workspace",
        resource_urn=w_urn,
        permission=request.relation,
        reason=f"revoked {request.relation} from {principal_urn}",
        extra={"targetPrincipalUrn": principal_urn},
    )

    return {"status": "revoked", "principalUrn": principal_urn, "relation": request.relation, "workspace_id": wid}



@router.get("/access")
async def list_workspace_access(
    workspace_id: str | None = None, principal: Principal = Depends(current_principal)
) -> list[dict]:
    """List principals holding access relations on the workspace."""
    tid, wid = await _resolve_workspace_governance(
        principal, tenant_id=principal.tenant_id, workspace_id=workspace_id
    )
    return await _access_listing("workspace", workspace_urn(tid, wid), VALID_WORKSPACE_RELATIONS)



@router.post("/projects", status_code=201)
async def create_project_endpoint(request: CreateProjectRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Create a project within a workspace."""
    tid, wid = await _resolve_workspace_governance(
        principal, tenant_id=principal.tenant_id, workspace_id=None
    )
    urn = project_urn(tid, wid, request.name)
    if await get_project(deps.pool, urn) is not None:
        raise HolonError.conflict('ProjectAlreadyExists', f"project already exists: {request.name}", name=request.name)
    project = await create_project(deps.pool, tenant_id=tid, workspace_id=wid, name=request.name)
    await deps.authz.write_relationship(
        resource_type="project",
        resource_urn=urn,
        relation="parent_workspace",
        subject_type="workspace",
        subject_urn=workspace_urn(tid, wid),
    )
    return project



@router.get("/projects")
async def list_projects_endpoint(principal: Principal = Depends(current_principal)) -> list[dict]:
    return await list_projects(deps.pool, principal.tenant_id)



@router.get("/projects/{name}")
async def get_project_endpoint(name: str, principal: Principal = Depends(current_principal)) -> dict:
    projects = await list_projects(deps.pool, principal.tenant_id)
    project = next((p for p in projects if p["name"] == name), None)
    if project is None:
        raise HolonError.not_found('ProjectNotFound', f"unknown project: {name}")
    return project



@router.post("/projects/{name}/principals/{principal_urn:path}/access/grant")
async def grant_project_access(
    name: str, principal_urn: str, request: AccessRequest, principal: Principal = Depends(current_principal)
) -> dict:
    p_urn = await _authorize_project_governance(principal, name)
    _validate_project_relation(request.relation)
    target = await _require_grant_target(principal_urn, tenant_id=principal.tenant_id)
    await deps.authz.write_relationship(
        resource_type="project",
        resource_urn=p_urn,
        relation=request.relation,
        subject_urn=principal_urn,
        optional_subject_relation=_grant_subject_relation(target),
    )
    await _enqueue_permission_event(
        event_type="identity.permission.granted",
        target_principal_urn=principal_urn,
        resource_type="project",
        resource_urn=p_urn,
        relation=request.relation,
        actor=principal,
    )
    if target.type == "group":
        await _fanout_group_permission_event(
            target,
            event_type="identity.permission.granted",
            resource_type="project",
            resource_urn=p_urn,
            relation=request.relation,
            actor=principal,
            tenant_id=principal.tenant_id,
        )
    return {"status": "granted", "principalUrn": principal_urn, "project": name, "relation": request.relation}



@router.post("/projects/{name}/principals/{principal_urn:path}/access/revoke")
async def revoke_project_access(
    name: str, principal_urn: str, request: AccessRequest, principal: Principal = Depends(current_principal)
) -> dict:
    p_urn = await _authorize_project_governance(principal, name)
    _validate_project_relation(request.relation)
    target = await _require_grant_target(principal_urn, tenant_id=principal.tenant_id)
    await deps.authz.delete_relationship(
        resource_type="project",
        resource_urn=p_urn,
        relation=request.relation,
        subject_urn=principal_urn,
        optional_subject_relation=_grant_subject_relation(target),
    )

    await _enqueue_permission_event(
        event_type="identity.permission.revoked",
        target_principal_urn=principal_urn,
        resource_type="project",
        resource_urn=p_urn,
        relation=request.relation,
        actor=principal,
    )
    if target.type == "group":
        await _fanout_group_permission_event(
            target,
            event_type="identity.permission.revoked",
            resource_type="project",
            resource_urn=p_urn,
            relation=request.relation,
            actor=principal,
            tenant_id=principal.tenant_id,
        )

    return {"status": "revoked", "principalUrn": principal_urn, "project": name, "relation": request.relation}



@router.get("/projects/{name}/access")
async def list_project_access(name: str, principal: Principal = Depends(current_principal)) -> list[dict]:
    """List principals with direct grants on the project."""
    p_urn = await _authorize_project_governance(principal, name)
    return await _access_listing("project", p_urn, VALID_PROJECT_RELATIONS)

