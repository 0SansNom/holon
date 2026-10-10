"""Connectivity sftp source routes."""
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
from ... import sftp_source_registry
from ...ingest import RegisterSftpConnectionRequest, RegisterSftpSourceRequest

router = APIRouter()


@router.post("/sftp-connections")
async def register_sftp_connection(
    body: RegisterSftpConnectionRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Register or update an SFTP connection credential."""
    await _authorize_workspace(principal, "write")
    try:
        return await sftp_source_registry.register_connection(
            deps.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            host=body.host,
            port=body.port,
            username=body.username,
            password=body.password,
            secret_ref=body.secret_ref,
            created_by_urn=principal.urn,
        )
    except sftp_source_registry.SourceConfigError as exc:
        raise HolonError.invalid_argument("SourceValidationFailed", str(exc)) from exc


@router.get("/sftp-connections")
async def list_sftp_connections(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await sftp_source_registry.list_connections(deps.pool, principal.tenant_id)


@router.delete("/sftp-connections/{name}")
async def delete_sftp_connection(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    if await sftp_source_registry.get_connection(deps.pool, principal.tenant_id, name) is None:
        raise HolonError.not_found("ConnectionNotFound", f"no SFTP connection registered as {name!r}", name=name)
    try:
        await sftp_source_registry.delete_connection(deps.pool, principal.tenant_id, name)
    except sftp_source_registry.ConnectionInUseError as exc:
        raise HolonError.conflict("ConnectionConflict", str(exc)) from exc
    return {"deleted": name}


@router.post("/sftp-sources")
async def register_sftp_source(
    body: RegisterSftpSourceRequest,
    principal: Principal = Depends(current_principal),
    workspace_id: Optional[str] = Query(None, alias="workspaceId"),
    x_holon_workspace_id: Optional[str] = Header(None, alias="X-Holon-Workspace-Id"),
) -> dict:
    """Register a new SFTP source."""
    target_workspace = _resolve_workspace(
        explicit=body.workspace_id,
        workspace_id=workspace_id,
        x_holon_workspace_id=x_holon_workspace_id,
    )
    await _authorize_workspace(principal, "write", workspace_id=target_workspace)
    existing = await _shared._authorize_source_update(sftp_source_registry, principal, body.name, target_workspace)
    try:
        registration = await sftp_source_registry.register_source(
            deps.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            workspace_id=target_workspace,
            connection_name=body.connection_name,
            format=body.format,
            remote_path=body.remote_path,
            remote_prefix=body.remote_prefix,
            incremental=body.incremental,
            schedule_interval_minutes=body.schedule_interval_minutes,
            created_by_urn=principal.urn,
            reserved_dataset_names=await _reserved_dataset_names(deps.pool),
        )
    except sftp_source_registry.SourceConflictError as exc:
        raise HolonError.conflict("SourceConflict", str(exc)) from exc
    except sftp_source_registry.SourceConfigError as exc:
        raise HolonError.invalid_argument("SourceValidationFailed", str(exc)) from exc
    await _seed_source_authz(
        tenant_id=principal.tenant_id,
        workspace_id=target_workspace,
        name=body.name,
        compensate_delete=None
        if existing is not None
        else lambda: sftp_source_registry.delete_source(deps.pool, principal.tenant_id, body.name),
    )
    emit_audit(
        category="access",
        action="connectivity.sftp_source.registered",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="source",
        resource_urn=source_urn(principal.tenant_id, target_workspace, body.name),
        extra={"connection_name": body.connection_name},
    )
    return registration


@router.get("/sftp-sources")
async def list_sftp_sources(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await _filter_readable(
        principal, "source", await sftp_source_registry.list_sources(deps.pool, principal.tenant_id)
    )


@router.post("/sftp-sources/{name}/disable")
async def disable_sftp_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    return await _shared._set_source_status(sftp_source_registry, principal, name, "disabled")


@router.post("/sftp-sources/{name}/enable")
async def enable_sftp_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    return await _shared._set_source_status(sftp_source_registry, principal, name, "active")


@router.delete("/sftp-sources/{name}")
async def delete_sftp_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    return await _shared._delete_registered_source(
        sftp_source_registry, principal, name, audit_action="connectivity.sftp_source.deleted"
    )

