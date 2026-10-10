"""Federated OIDC/SAML login completion (session cookie + group sync)."""
from __future__ import annotations

import asyncpg
from fastapi.responses import RedirectResponse

from holon_common import HolonError, set_session_cookie
from holon_common.audit import emit_audit

from . import deps
from .governance import _delete_relationship_or_reraise
from .seed import (
    VALID_WORKSPACE_RELATIONS,
    get_tenant,
    get_workspace,
    insert_principal,
    list_workspaces,
    tenant_urn,
    workspace_urn,
)


async def _complete_federated_login(
    *,
    protocol: str,
    external_id: str,
    tenant_id: str,
    local_name: str,
    display_name: str,
    workspace_roles: dict[str, str],
    frontend_redirect: str,
) -> RedirectResponse:
    """Complete federated OIDC/SAML login and issue session cookie."""
    lookup_column = "oidc_sub" if protocol == "oidc" else "external_id"
    audit_action = f"identity.{protocol}.login"
    error_name = deps._FEDERATED_ERROR_NAME[protocol]

    tenant = await get_tenant(deps.pool, tenant_id)
    if tenant is None or tenant["status"] != "active":
        emit_audit(
            category="identity",
            action=audit_action,
            outcome="failure",
            tenant_id=tenant_id,
            actor_urn=external_id,
            reason=f"unknown or disabled tenant: {tenant_id}",
        )
        raise HolonError.forbidden('TenantDisabled', f"unknown or disabled tenant for {protocol} login: {tenant_id}")

    row = await deps.pool.fetchrow(f"SELECT * FROM principal WHERE {lookup_column} = $1", external_id)
    if row is None:
        try:
            created = await insert_principal(
                deps.pool,
                tenant_id=tenant_id,
                type="user",
                local_name=local_name,
                display_name=display_name,
                **{lookup_column: external_id},
            )
        except asyncpg.UniqueViolationError as exc:
            raise HolonError.conflict(
                "FederatedLocalNameConflict",
                f"{protocol} identity maps to local_name {local_name!r} which already exists; "
                "refusing to attach this IdP subject to the existing principal",
            ) from exc
        await deps.authz.write_relationship(
            resource_type="tenant",
            resource_urn=tenant_urn(tenant_id),
            relation="member",
            subject_urn=created["urn"],
        )
        row = await deps.pool.fetchrow("SELECT * FROM principal WHERE urn = $1", created["urn"])
    else:
        if row["tenant_id"] != tenant_id:
            emit_audit(
                category="identity",
                action=audit_action,
                outcome="failure",
                tenant_id=row["tenant_id"],
                actor_urn=row["urn"],
                reason=f"{protocol} tenant claim mismatch",
            )
            raise HolonError.forbidden(error_name, (
                    f"{protocol} tenant claim {tenant_id!r} does not match linked principal "
                    f"tenant {row['tenant_id']!r}; unlink {lookup_column} or update the principal"
                ),)

    if row["status"] != "active":
        raise HolonError.forbidden('PrincipalDisabled', "principal is disabled")
    principal = deps._principal_from_row(row)

    # Group → workspace relation sync (admin/editor/viewer). Highest privilege wins;
    # alternate relations on the same workspace are removed for this principal.
    # Workspaces that disappeared from the IdP token are revoked (day-2 SSO).
    desired_ids = set(workspace_roles)
    for ws in await list_workspaces(deps.pool, principal.tenant_id):
        if ws["workspace_id"] in desired_ids:
            continue
        w_urn = workspace_urn(principal.tenant_id, ws["workspace_id"])
        for relation in VALID_WORKSPACE_RELATIONS:
            await _delete_relationship_or_reraise(
                resource_type="workspace",
                resource_urn=w_urn,
                relation=relation,
                subject_urn=principal.urn,
            )
    synced: list[dict] = []
    for workspace_id, relation in workspace_roles.items():
        ws = await get_workspace(deps.pool, workspace_id)
        if ws is None or ws["tenant_id"] != principal.tenant_id:
            continue
        w_urn = workspace_urn(principal.tenant_id, workspace_id)
        await deps.authz.write_relationship(
            resource_type="workspace",
            resource_urn=w_urn,
            relation=relation,
            subject_urn=principal.urn,
        )
        for other in VALID_WORKSPACE_RELATIONS - {relation}:
            await _delete_relationship_or_reraise(
                resource_type="workspace",
                resource_urn=w_urn,
                relation=other,
                subject_urn=principal.urn,
            )
        synced.append({"workspaceId": workspace_id, "relation": relation})
        emit_audit(
            category="identity",
            action=f"identity.{protocol}.group_sync",
            outcome="success",
            tenant_id=principal.tenant_id,
            actor_urn=principal.urn,
            actor_type=principal.type,
            resource_type="workspace",
            resource_urn=w_urn,
            permission=relation,
            extra={"source": f"{protocol}_groups"},
        )

    emit_audit(
        category="identity",
        action=audit_action,
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        extra={"syncedWorkspaces": synced},
    )

    redirect = RedirectResponse(url=frontend_redirect, status_code=302)
    set_session_cookie(redirect, deps._issue(principal))
    return redirect