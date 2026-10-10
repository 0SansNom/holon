"""Connectivity object store source routes."""
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
from ... import object_source_registry
from ...ingest import RegisterObjectConnectionRequest, RegisterObjectSourceRequest

router = APIRouter()


@router.post("/object-connections")
async def register_object_connection(
    body: RegisterObjectConnectionRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Register or update an object storage connection credential (S3-compatible or Azure Blob)."""
    await _authorize_workspace(principal, "write")
    try:
        return await object_source_registry.register_connection(
            deps.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            kind=body.kind,
            endpoint=body.endpoint,
            access_key_id=body.access_key_id,
            region=body.region,
            path_style=body.path_style,
            secret_access_key=body.secret_access_key,
            secret_ref=body.secret_ref,
            created_by_urn=principal.urn,
        )
    except object_source_registry.SourceConfigError as exc:
        raise HolonError.invalid_argument("SourceValidationFailed", str(exc)) from exc


@router.get("/object-connections")
async def list_object_connections(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await object_source_registry.list_connections(deps.pool, principal.tenant_id)


@router.delete("/object-connections/{name}")
async def delete_object_connection(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    if await object_source_registry.get_connection(deps.pool, principal.tenant_id, name) is None:
        raise HolonError.not_found('ConnectionNotFound', f"no object connection registered as {name!r}", name=name)
    try:
        await object_source_registry.delete_connection(deps.pool, principal.tenant_id, name)
    except object_source_registry.ConnectionInUseError as exc:
        raise HolonError.conflict('ConnectionConflict', str(exc)) from exc
    return {"deleted": name}


@router.post("/object-sources")
async def register_object_source(
    body: RegisterObjectSourceRequest,
    principal: Principal = Depends(current_principal),
    workspace_id: Optional[str] = Query(None, alias="workspaceId"),
    x_holon_workspace_id: Optional[str] = Header(None, alias="X-Holon-Workspace-Id"),
) -> dict:
    """Register a new object storage source."""
    target_workspace = _resolve_workspace(
        explicit=body.workspace_id,
        workspace_id=workspace_id,
        x_holon_workspace_id=x_holon_workspace_id,
    )
    await _authorize_workspace(principal, "write", workspace_id=target_workspace)
    existing = await _shared._authorize_source_update(object_source_registry, principal, body.name, target_workspace)
    try:
        registration = await object_source_registry.register_source(
            deps.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            workspace_id=target_workspace,
            connection_name=body.connection_name,
            bucket=body.bucket,
            format=body.format,
            object_key=body.object_key,
            key_prefix=body.key_prefix,
            incremental=body.incremental,
            schedule_interval_minutes=body.schedule_interval_minutes,
            created_by_urn=principal.urn,
            reserved_dataset_names=await _reserved_dataset_names(deps.pool),
        )
    except object_source_registry.SourceConflictError as exc:
        raise HolonError.conflict('SourceConflict', str(exc)) from exc
    except object_source_registry.SourceConfigError as exc:
        raise HolonError.invalid_argument('SourceValidationFailed', str(exc)) from exc
    await _seed_source_authz(
        tenant_id=principal.tenant_id,
        workspace_id=target_workspace,
        name=body.name,
        compensate_delete=None
        if existing is not None
        else lambda: object_source_registry.delete_source(deps.pool, principal.tenant_id, body.name),
    )
    emit_audit(
        category="access",
        action="connectivity.object_source.registered",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="source",
        resource_urn=source_urn(principal.tenant_id, target_workspace, body.name),
        extra={"connection_name": body.connection_name},
    )
    return registration


@router.get("/object-sources")
async def list_object_sources(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await _filter_readable(
        principal, "source", await object_source_registry.list_sources(deps.pool, principal.tenant_id)
    )


@router.post("/object-sources/{name}/disable")
async def disable_object_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    return await _shared._set_source_status(object_source_registry, principal, name, "disabled")


@router.post("/object-sources/{name}/enable")
async def enable_object_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    return await _shared._set_source_status(object_source_registry, principal, name, "active")


@router.delete("/object-sources/{name}")
async def delete_object_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    return await _shared._delete_registered_source(
        object_source_registry, principal, name, audit_action="connectivity.object_source.deleted"
    )

