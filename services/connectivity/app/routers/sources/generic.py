"""Connectivity generic source routes."""
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
from ... import generic_source_registry
from ...ingest import RegisterConnectionRequest, RegisterSourceRequest

router = APIRouter()


@router.post("/connections")
async def register_connection(body: RegisterConnectionRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Register or update a REST connection credential."""
    await _authorize_workspace(principal, "write")
    try:
        return await generic_source_registry.register_connection(
            deps.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            auth_type=body.auth_type,
            auth_header_name=body.auth_header_name,
            auth_header_value=body.auth_header_value,
            oauth2_token_url=body.oauth2_token_url,
            oauth2_client_id=body.oauth2_client_id,
            oauth2_client_secret=body.oauth2_client_secret,
            oauth2_scope=body.oauth2_scope,
            secret_ref=body.secret_ref,
            allowed_origin=body.allowed_origin,
            created_by_urn=principal.urn,
        )
    except generic_source_registry.SourceConfigError as exc:
        raise HolonError.invalid_argument('ConnectionValidationFailed', str(exc)) from exc


@router.get("/connections")
async def list_connections(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await generic_source_registry.list_connections(deps.pool, principal.tenant_id)


@router.delete("/connections/{name}")
async def delete_connection(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    if await generic_source_registry.get_connection(deps.pool, principal.tenant_id, name) is None:
        raise HolonError.not_found('ConnectionNotFound', f"no connection registered as {name!r}", name=name)
    try:
        await generic_source_registry.delete_connection(deps.pool, principal.tenant_id, name)
    except generic_source_registry.ConnectionInUseError as exc:
        raise HolonError.conflict('ConnectionConflict', str(exc)) from exc
    return {"deleted": name}

@router.post("/sources")
async def register_source(
    body: RegisterSourceRequest,
    principal: Principal = Depends(current_principal),
    workspace_id: Optional[str] = Query(None, alias="workspaceId"),
    x_holon_workspace_id: Optional[str] = Header(None, alias="X-Holon-Workspace-Id"),
) -> dict:
    """Register a new REST API source."""
    target_workspace = _resolve_workspace(
        explicit=body.workspace_id,
        workspace_id=workspace_id,
        x_holon_workspace_id=x_holon_workspace_id,
    )
    await _authorize_workspace(principal, "write", workspace_id=target_workspace)
    existing = await _shared._authorize_source_update(generic_source_registry, principal, body.name, target_workspace)
    try:
        registration = await generic_source_registry.register_source(
            deps.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            base_url=body.base_url,
            created_by_urn=principal.urn,
            workspace_id=target_workspace,
            auth_header_name=body.auth_header_name,
            auth_header_value=body.auth_header_value,
            record_path=body.record_path,
            next_page_path=body.next_page_path,
            connection_name=body.connection_name,
            schedule_interval_minutes=body.schedule_interval_minutes,
            cursor_property=body.cursor_property,
            incremental_param=body.incremental_param,
            reserved_dataset_names=await _reserved_dataset_names(deps.pool),
        )
    except generic_source_registry.SourceConflictError as exc:
        raise HolonError.conflict('PluginConflict', str(exc)) from exc
    except generic_source_registry.SourceConfigError as exc:
        raise HolonError.invalid_argument('PluginValidationFailed', str(exc)) from exc
    await _seed_source_authz(
        tenant_id=principal.tenant_id,
        workspace_id=target_workspace,
        name=body.name,
        compensate_delete=None
        if existing is not None
        else lambda: generic_source_registry.delete_source(deps.pool, principal.tenant_id, body.name),
    )
    emit_audit(
        category="access",
        action="connectivity.source.registered",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="source",
        resource_urn=source_urn(principal.tenant_id, target_workspace, body.name),
        extra={"base_url": body.base_url},
    )
    return registration


@router.get("/sources")
async def list_sources(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await _filter_readable(
        principal, "source", await generic_source_registry.list_sources(deps.pool, principal.tenant_id)
    )


@router.post("/sources/{name}/disable")
async def disable_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    return await _shared._set_source_status(generic_source_registry, principal, name, "disabled")


@router.post("/sources/{name}/enable")
async def enable_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    return await _shared._set_source_status(generic_source_registry, principal, name, "active")


@router.delete("/sources/{name}")
async def delete_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    return await _shared._delete_registered_source(
        generic_source_registry, principal, name, audit_action="connectivity.source.deleted"
    )

