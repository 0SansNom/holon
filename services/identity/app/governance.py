"""Identity workspace / project / bootstrap governance and listing."""
from __future__ import annotations

import httpx

from holon_common import HolonError, Principal

from . import deps
from .seed import (
    VALID_PROJECT_RELATIONS,
    VALID_WORKSPACE_RELATIONS,
    get_workspace,
    list_projects,
    list_workspaces,
    workspace_urn,
)


async def _authorize_bootstrap_governance(principal: Principal) -> None:
    """Authorize tenant creation on the bootstrap workspace."""
    decision = await deps.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=workspace_urn(deps.TENANT_ID, deps.WORKSPACE_ID),
        permission="approve",
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)

async def _authorize_workspace_governance(principal: Principal, tenant_id: str, workspace_id: str) -> str:
    ws = await get_workspace(deps.pool, workspace_id)
    if ws is None or ws["tenant_id"] != tenant_id:
        raise HolonError.not_found('WorkspaceNotFound', f"unknown workspace: {workspace_id}")
    if ws["status"] != "active":
        raise HolonError.invalid_argument('WorkspaceDisabled', "workspace is disabled")
    w_urn = workspace_urn(tenant_id, workspace_id)
    decision = await deps.authz.authorize(
        principal, resource_type="workspace", resource_urn=w_urn, permission="approve"
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)
    return w_urn

async def _delete_relationship_or_reraise(
    *,
    resource_type: str,
    resource_urn: str,
    relation: str,
    subject_urn: str,
) -> None:
    """Idempotently delete SpiceDB relationship, erroring on store unavailability."""
    try:
        await deps.authz.delete_relationship(
            resource_type=resource_type,
            resource_urn=resource_urn,
            relation=relation,
            subject_urn=subject_urn,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return
        raise HolonError.unavailable("SpiceDbUnavailable", "authorization service error during grant sync") from exc
    except httpx.RequestError as exc:
        raise HolonError.unavailable("SpiceDbUnavailable", "authorization service unreachable during grant sync") from exc

async def _authorize_principal_governance(principal: Principal, tenant_id: str) -> None:
    workspaces = await list_workspaces(deps.pool, tenant_id)
    if not workspaces:
        await _authorize_bootstrap_governance(principal)
    else:
        await _authorize_workspace_governance(principal, tenant_id, workspaces[0]["workspace_id"])

async def _resolve_workspace_governance(
    principal: Principal, *, tenant_id: str, workspace_id: str | None
) -> tuple[str, str]:
    """Authorize workspace governance permissions for a tenant."""
    if workspace_id:
        await _authorize_workspace_governance(principal, tenant_id, workspace_id)
        return tenant_id, workspace_id
    if tenant_id == deps.TENANT_ID:
        await _authorize_workspace_governance(principal, deps.TENANT_ID, deps.WORKSPACE_ID)
        return deps.TENANT_ID, deps.WORKSPACE_ID
    workspaces = await list_workspaces(deps.pool, tenant_id)
    if not workspaces:
        raise HolonError.invalid_argument('TenantHasNoWorkspace', f"tenant {tenant_id!r} has no workspace", tenant_id=tenant_id)
    last_exc: HolonError | None = None
    for ws in workspaces:
        try:
            await _authorize_workspace_governance(principal, tenant_id, ws["workspace_id"])
            return tenant_id, ws["workspace_id"]
        except HolonError as exc:
            if exc.status_code == 403:
                last_exc = exc
                continue
            raise
    raise last_exc or HolonError.forbidden(
        "PermissionDenied", "access denied: workspace approve required"
    )

async def _access_listing(resource_type: str, resource_urn: str, valid_relations: set[str]) -> list[dict]:
    """Enumerate direct ReBAC grants on a resource."""
    from holon_common.spicedb_id import index_by_spicedb_object_id

    relationships = await deps.authz.read_relationships(resource_type=resource_type, resource_urn=resource_urn)
    rows = await deps.pool.fetch("SELECT * FROM principal")
    by_object_id = index_by_spicedb_object_id(rows)

    grants = []
    for rel in relationships:
        relation = rel.get("relation", "")
        subject = rel.get("subject", {}).get("object", {})
        if relation not in valid_relations or subject.get("objectType") != "principal":
            continue  # parent_tenant/parent_workspace edges are hierarchy, not access grants
        subject_id = subject.get("objectId", "")
        row = by_object_id.get(subject_id)
        grants.append(
            {
                "principal_urn": row["urn"] if row else subject_id,
                "display_name": row["display_name"] if row else None,
                "type": row["type"] if row else None,
                "relation": relation,
            }
        )
    return sorted(grants, key=lambda g: (g["principal_urn"], g["relation"]))

def _validate_relation(relation: str) -> None:
    if relation not in VALID_WORKSPACE_RELATIONS:
        raise HolonError.invalid_argument('InvalidWorkspaceRelation', f"invalid relation: {relation!r} (must be one of {sorted(VALID_WORKSPACE_RELATIONS)})",
        )

async def _authorize_project_governance(principal: Principal, project_name: str) -> str:
    """Authorize project governance permissions."""
    projects = await list_projects(deps.pool, principal.tenant_id)
    project = next((p for p in projects if p["name"] == project_name), None)
    if project is None:
        raise HolonError.not_found('ProjectNotFound', f"unknown project: {project_name}")
    urn = project["urn"]
    decision = await deps.authz.authorize(principal, resource_type="project", resource_urn=urn, permission="approve")
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)
    return urn

def _validate_project_relation(relation: str) -> None:
    if relation not in VALID_PROJECT_RELATIONS:
        raise HolonError.invalid_argument('InvalidProjectRelation', f"invalid relation: {relation!r} (must be one of {sorted(VALID_PROJECT_RELATIONS)})",
        )