"""Connectivity salesforce source routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, Query

from holon_common import HolonError, Principal
from holon_common.audit import emit_audit

from ... import deps
from ...deps import (
    _authorize_workspace,
    _filter_readable,
    _reserved_dataset_names,
    _resolve_workspace,
    _seed_source_authz,
    current_principal,
    source_urn,
)
from . import _shared

router = APIRouter()

from ... import salesforce_source_registry
from ...ingest import RegisterSalesforceConnectionRequest, RegisterSalesforceSourceRequest

@router.post("/salesforce-connections")
async def register_salesforce_connection(
    body: RegisterSalesforceConnectionRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Register or update a Salesforce Connected App credential."""
    await _authorize_workspace(principal, "write")
    try:
        return await salesforce_source_registry.register_connection(
            deps.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            client_id=body.client_id,
            created_by_urn=principal.urn,
            login_url=body.login_url,
            client_secret=body.client_secret,
            secret_ref=body.secret_ref,
        )
    except salesforce_source_registry.SourceConfigError as exc:
        raise HolonError.invalid_argument("SourceValidationFailed", str(exc)) from exc


@router.get("/salesforce-connections")
async def list_salesforce_connections(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await salesforce_source_registry.list_connections(deps.pool, principal.tenant_id)


@router.delete("/salesforce-connections/{name}")
async def delete_salesforce_connection(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    if await salesforce_source_registry.get_connection(deps.pool, principal.tenant_id, name) is None:
        raise HolonError.not_found("ConnectionNotFound", f"no Salesforce connection registered as {name!r}", name=name)
    try:
        await salesforce_source_registry.delete_connection(deps.pool, principal.tenant_id, name)
    except salesforce_source_registry.ConnectionInUseError as exc:
        raise HolonError.conflict("ConnectionConflict", str(exc)) from exc
    return {"deleted": name}


@router.post("/salesforce-sources")
async def register_salesforce_source(
    body: RegisterSalesforceSourceRequest,
    principal: Principal = Depends(current_principal),
    workspace_id: Optional[str] = Query(None, alias="workspaceId"),
    x_holon_workspace_id: Optional[str] = Header(None, alias="X-Holon-Workspace-Id"),
) -> dict:
    """Register a Salesforce SOQL source."""
    target_workspace = _resolve_workspace(
        explicit=body.workspace_id,
        workspace_id=workspace_id,
        x_holon_workspace_id=x_holon_workspace_id,
    )
    await _authorize_workspace(principal, "write", workspace_id=target_workspace)
    existing = await _shared._authorize_source_update(salesforce_source_registry, principal, body.name, target_workspace)
    try:
        registration = await salesforce_source_registry.register_source(
            deps.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            workspace_id=target_workspace,
            connection_name=body.connection_name,
            soql=body.soql,
            created_by_urn=principal.urn,
            api_version=body.api_version,
            cursor_property=body.cursor_property,
            schedule_interval_minutes=body.schedule_interval_minutes,
            reserved_dataset_names=await _reserved_dataset_names(deps.pool),
        )
    except salesforce_source_registry.SourceConflictError as exc:
        raise HolonError.conflict("SourceConflict", str(exc)) from exc
    except salesforce_source_registry.SourceConfigError as exc:
        raise HolonError.invalid_argument("SourceValidationFailed", str(exc)) from exc
    await _seed_source_authz(
        tenant_id=principal.tenant_id,
        workspace_id=target_workspace,
        name=body.name,
        compensate_delete=None
        if existing is not None
        else lambda: salesforce_source_registry.delete_source(deps.pool, principal.tenant_id, body.name),
    )
    emit_audit(
        category="access",
        action="connectivity.salesforce_source.registered",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="source",
        resource_urn=source_urn(principal.tenant_id, target_workspace, body.name),
        extra={"connection_name": body.connection_name},
    )
    return registration


@router.get("/salesforce-sources")
async def list_salesforce_sources(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await _filter_readable(
        principal, "source", await salesforce_source_registry.list_sources(deps.pool, principal.tenant_id)
    )


@router.post("/salesforce-sources/{name}/disable")
async def disable_salesforce_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    return await _shared._set_source_status(salesforce_source_registry, principal, name, "disabled")


@router.post("/salesforce-sources/{name}/enable")
async def enable_salesforce_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    return await _shared._set_source_status(salesforce_source_registry, principal, name, "active")


@router.delete("/salesforce-sources/{name}")
async def delete_salesforce_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    return await _shared._delete_registered_source(
        salesforce_source_registry, principal, name, audit_action="connectivity.salesforce_source.deleted"
    )

