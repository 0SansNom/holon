"""Tenants, workspaces, principals, groups."""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends

from holon_common import HolonError, Principal
from holon_common.audit import emit_audit

from .. import deps
from ..deps import (
    CreatePrincipalRequest,
    CreateTenantRequest,
    CreateWorkspaceRequest,
    GroupMemberRequest,
    StatusRequest,
    _access_listing,
    _authorize_bootstrap_governance,
    _authorize_principal_governance,
    _authorize_workspace_governance,
    _enqueue_permission_event,
    _enqueue_principal_status_event,
    _fetch_principal,
    _principal_from_row,
    _require_grant_target,
    _require_group,
    current_principal,
)
from ..seed import (
    create_tenant,
    create_workspace,
    get_tenant,
    get_workspace,
    insert_principal,
    list_tenants,
    list_workspaces,
    set_tenant_status,
    set_workspace_status,
    tenant_urn,
    workspace_urn,
)


router = APIRouter()


@router.get("/principals", response_model=list[Principal])
async def list_principals(principal: Principal = Depends(current_principal)) -> list[Principal]:
    """List principals belonging to the caller's tenant."""
    rows = await deps.pool.fetch(
        "SELECT * FROM principal WHERE tenant_id = $1 ORDER BY urn", principal.tenant_id
    )
    return [_principal_from_row(row) for row in rows]



@router.get("/tenants")
async def tenants_list(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_bootstrap_governance(principal)
    return await list_tenants(deps.pool)



@router.post("/tenants", status_code=201)
async def tenants_create(request: CreateTenantRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Create a new tenant."""
    await _authorize_bootstrap_governance(principal)
    if await get_tenant(deps.pool, request.tenant_id) is not None:
        raise HolonError.conflict('TenantAlreadyExists', f"tenant already exists: {request.tenant_id}")
    return await create_tenant(deps.pool, tenant_id=request.tenant_id, display_name=request.display_name)



@router.post("/tenants/{tenant_id}/status")
async def tenants_set_status(
    tenant_id: str, request: StatusRequest, principal: Principal = Depends(current_principal)
) -> dict:
    await _authorize_bootstrap_governance(principal)
    if await get_tenant(deps.pool, tenant_id) is None:
        raise HolonError.not_found('TenantNotFound', f"unknown tenant: {tenant_id}")
    updated = await set_tenant_status(deps.pool, tenant_id, request.status)
    return updated  # type: ignore[return-value]



@router.get("/workspaces")
async def workspaces_list(
    tenant_id: str | None = None, principal: Principal = Depends(current_principal)
) -> list[dict]:
    # Callers see workspaces in their own tenant unless bootstrap admin lists all.
    try:
        await _authorize_bootstrap_governance(principal)
        return await list_workspaces(deps.pool, tenant_id)
    except HolonError:
        return await list_workspaces(deps.pool, principal.tenant_id)



@router.post("/workspaces", status_code=201)
async def workspaces_create(
    request: CreateWorkspaceRequest, principal: Principal = Depends(current_principal)
) -> dict:
    tenant = await get_tenant(deps.pool, request.tenant_id)
    if tenant is None:
        raise HolonError.not_found('TenantNotFound', f"unknown tenant: {request.tenant_id}")
    if tenant["status"] != "active":
        raise HolonError.invalid_argument('TenantDisabled', "tenant is disabled")
    # Bootstrap admins may create the first workspace on a new filiale;
    # otherwise require approve on an existing workspace in that tenant.
    existing = await list_workspaces(deps.pool, request.tenant_id)
    if not existing:
        await _authorize_bootstrap_governance(principal)
    else:
        await _authorize_workspace_governance(principal, request.tenant_id, existing[0]["workspace_id"])
    if await get_workspace(deps.pool, request.workspace_id) is not None:
        raise HolonError.conflict('WorkspaceAlreadyExists', f"workspace already exists: {request.workspace_id}")

    # Never grant workspace admin to a principal from another tenant —
    # instance admins nominate a same-tenant `initial_admin_urn`.
    if principal.tenant_id == request.tenant_id:
        admin_urn = principal.urn
    else:
        if not request.initial_admin_urn:
            raise HolonError.invalid_argument('InitialAdminRequired', "initial_admin_urn is required when creating a workspace outside your tenant "
                "(create the filiale principal first, then pass their URN)",)
        admin = await _fetch_principal(deps.pool, request.initial_admin_urn)
        if admin is None:
            raise HolonError.not_found('PrincipalNotFound', f"unknown principal: {request.initial_admin_urn}")
        if admin.tenant_id != request.tenant_id:
            raise HolonError.invalid_argument('InitialAdminTenantMismatch', "initial_admin_urn must belong to the workspace's tenant")
        admin_urn = admin.urn

    workspace = await create_workspace(
        deps.pool,
        tenant_id=request.tenant_id,
        workspace_id=request.workspace_id,
        display_name=request.display_name,
    )
    await deps.authz.write_relationship(
        resource_type="workspace",
        resource_urn=workspace_urn(request.tenant_id, request.workspace_id),
        relation="parent_tenant",
        subject_type="tenant",
        subject_urn=tenant_urn(request.tenant_id),
    )
    await deps.authz.write_relationship(
        resource_type="workspace",
        resource_urn=workspace_urn(request.tenant_id, request.workspace_id),
        relation="admin",
        subject_urn=admin_urn,
    )
    return workspace



@router.post("/workspaces/{workspace_id}/status")
async def workspaces_set_status(
    workspace_id: str, request: StatusRequest, principal: Principal = Depends(current_principal)
) -> dict:
    ws = await get_workspace(deps.pool, workspace_id)
    if ws is None:
        raise HolonError.not_found('WorkspaceNotFound', f"unknown workspace: {workspace_id}")
    await _authorize_workspace_governance(principal, ws["tenant_id"], workspace_id)
    updated = await set_workspace_status(deps.pool, workspace_id, request.status)
    return updated  # type: ignore[return-value]



@router.post("/principals", status_code=201)
async def principals_create(
    request: CreatePrincipalRequest, principal: Principal = Depends(current_principal)
) -> dict:
    tenant = await get_tenant(deps.pool, request.tenant_id)
    if tenant is None:
        raise HolonError.not_found('TenantNotFound', f"unknown tenant: {request.tenant_id}")
    workspaces = await list_workspaces(deps.pool, request.tenant_id)
    if not workspaces:
        await _authorize_bootstrap_governance(principal)
    else:
        await _authorize_workspace_governance(principal, request.tenant_id, workspaces[0]["workspace_id"])
    if request.type == "group" and request.on_behalf_of:
        raise HolonError.invalid_argument("GroupCannotDelegate", "a group cannot act on behalf of another principal")
    try:
        row = await insert_principal(
            deps.pool,
            tenant_id=request.tenant_id,
            type=request.type,
            local_name=request.local_name,
            display_name=request.display_name,
            country=request.country,
            on_behalf_of=request.on_behalf_of,
            client_secret=request.client_secret,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HolonError.conflict('PrincipalAlreadyExists', "principal already exists") from exc
    await deps.authz.write_relationship(
        resource_type="tenant",
        resource_urn=tenant_urn(request.tenant_id),
        relation="member",
        subject_urn=row["urn"],
    )
    # Never return client_secret in list form; include once at create for
    # service accounts / users. Groups cannot authenticate.
    payload = {
        "urn": row["urn"],
        "type": row["type"],
        "tenant_id": row["tenant_id"],
        "display_name": row["display_name"],
        "on_behalf_of": row["on_behalf_of"],
        "country": row["country"],
        "status": row["status"],
    }
    if request.type != "group":
        payload["client_secret"] = row["client_secret"]
    return payload



@router.post("/principals/{principal_urn:path}/status")
async def principals_set_status(
    principal_urn: str, request: StatusRequest, principal: Principal = Depends(current_principal)
) -> dict:
    target = await _fetch_principal(deps.pool, principal_urn)
    if target is None:
        raise HolonError.not_found('PrincipalNotFound', f"unknown principal: {principal_urn}")
    workspaces = await list_workspaces(deps.pool, target.tenant_id)
    if not workspaces:
        await _authorize_bootstrap_governance(principal)
    else:
        await _authorize_workspace_governance(principal, target.tenant_id, workspaces[0]["workspace_id"])
    updated = await _enqueue_principal_status_event(
        target_principal_urn=principal_urn,
        status=request.status,
        actor=principal,
        tenant_id=target.tenant_id,
    )
    assert updated is not None
    return {k: updated[k] for k in ("urn", "type", "tenant_id", "display_name", "status")}



@router.get("/principals/{group_urn:path}/members")
async def list_group_members(group_urn: str, principal: Principal = Depends(current_principal)) -> list[dict]:
    group = await _require_group(group_urn)
    if group.tenant_id != principal.tenant_id:
        raise HolonError.invalid_argument("CrossTenantPrincipal", "principal belongs to another tenant")
    return await _access_listing("principal", group_urn, {"member"})



@router.post("/principals/{group_urn:path}/members", status_code=201)
async def add_group_member(
    group_urn: str, request: GroupMemberRequest, principal: Principal = Depends(current_principal)
) -> dict:
    group = await _require_group(group_urn)
    await _authorize_principal_governance(principal, group.tenant_id)
    member = await _require_grant_target(request.principal_urn, tenant_id=group.tenant_id)
    if member.type == "group":
        raise HolonError.invalid_argument("NestedGroupForbidden", "group membership is one level only")
    if member.urn == group.urn:
        raise HolonError.invalid_argument("GroupCannotContainSelf", "a group cannot contain itself")
    await deps.authz.write_relationship(
        resource_type="principal",
        resource_urn=group.urn,
        relation="member",
        subject_urn=member.urn,
    )
    await _enqueue_permission_event(
        event_type="identity.permission.granted",
        target_principal_urn=member.urn,
        resource_type="principal",
        resource_urn=group.urn,
        relation="member",
        actor=principal,
        tenant_id=group.tenant_id,
    )
    emit_audit(
        category="identity",
        action="identity.group.member_added",
        outcome="success",
        tenant_id=group.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="principal",
        resource_urn=group.urn,
        extra={"memberUrn": member.urn},
    )
    return {"status": "added", "groupUrn": group.urn, "memberUrn": member.urn}



@router.delete("/principals/{group_urn:path}/members/{member_urn:path}")
async def remove_group_member(
    group_urn: str, member_urn: str, principal: Principal = Depends(current_principal)
) -> dict:
    group = await _require_group(group_urn)
    await _authorize_principal_governance(principal, group.tenant_id)
    member = await _require_grant_target(member_urn, tenant_id=group.tenant_id)
    await deps.authz.delete_relationship(
        resource_type="principal",
        resource_urn=group.urn,
        relation="member",
        subject_urn=member.urn,
    )
    await _enqueue_permission_event(
        event_type="identity.permission.revoked",
        target_principal_urn=member.urn,
        resource_type="principal",
        resource_urn=group.urn,
        relation="member",
        actor=principal,
        tenant_id=group.tenant_id,
    )
    emit_audit(
        category="identity",
        action="identity.group.member_removed",
        outcome="success",
        tenant_id=group.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="principal",
        resource_urn=group.urn,
        extra={"memberUrn": member.urn},
    )
    return {"status": "removed", "groupUrn": group.urn, "memberUrn": member.urn}

