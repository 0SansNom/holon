"""Connectivity sources routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, Query

from holon_common import HolonError, Principal
from holon_common.audit import emit_audit

from .. import deps, generic_source_registry, object_source_registry, sftp_source_registry, sql_source_registry
from ..deps import (
    _authorize_source,
    _authorize_workspace,
    _filter_readable,
    _reserved_dataset_names,
    _resolve_workspace,
    _seed_source_authz,
    _unlink_resource_authz,
    current_principal,
    resource_workspace,
    source_urn,
)
from ..ingest import (
    RegisterConnectionRequest,
    RegisterObjectConnectionRequest,
    RegisterObjectSourceRequest,
    RegisterSftpConnectionRequest,
    RegisterSftpSourceRequest,
    RegisterSourceRequest,
    RegisterSqlConnectionRequest,
    RegisterSqlSourceRequest,
    _source_not_found,
)


router = APIRouter()


async def _authorize_source_update(
    registry, principal: Principal, name: str, target_workspace: str
) -> Optional[dict]:
    """Re-registration is an upsert: require write on the existing source and
    keep it in its workspace (a move would leave a stale parent_workspace)."""
    existing = await registry.get_source(deps.pool, principal.tenant_id, name)
    if existing is None:
        return None
    stored = resource_workspace(existing)
    await _authorize_source(principal, "write", name=name, workspace_id=stored)
    if stored != target_workspace:
        raise HolonError.invalid_argument(
            "SourceWorkspaceMismatch",
            f"source {name!r} is bound to workspace {stored!r}; got {target_workspace!r}",
            name=name,
            expected_workspace_id=stored,
            got_workspace_id=target_workspace,
        )
    return existing


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


@router.post("/sql-connections")
async def register_sql_connection(
    body: RegisterSqlConnectionRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Register or update a SQL connection credential."""
    await _authorize_workspace(principal, "write")
    try:
        return await sql_source_registry.register_connection(
            deps.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            host=body.host,
            dialect=body.dialect,
            port=body.port,
            database=body.database,
            username=body.username,
            password=body.password,
            secret_ref=body.secret_ref,
            created_by_urn=principal.urn,
        )
    except sql_source_registry.SourceConfigError as exc:
        raise HolonError.invalid_argument("SourceValidationFailed", str(exc)) from exc


@router.get("/sql-connections")
async def list_sql_connections(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await sql_source_registry.list_connections(deps.pool, principal.tenant_id)


@router.delete("/sql-connections/{name}")
async def delete_sql_connection(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    if await sql_source_registry.get_connection(deps.pool, principal.tenant_id, name) is None:
        raise HolonError.not_found('ConnectionNotFound', f"no SQL connection registered as {name!r}", name=name)
    try:
        await sql_source_registry.delete_connection(deps.pool, principal.tenant_id, name)
    except sql_source_registry.ConnectionInUseError as exc:
        raise HolonError.conflict('ConnectionConflict', str(exc)) from exc
    return {"deleted": name}


@router.post("/sql-sources")
async def register_sql_source(
    body: RegisterSqlSourceRequest,
    principal: Principal = Depends(current_principal),
    workspace_id: Optional[str] = Query(None, alias="workspaceId"),
    x_holon_workspace_id: Optional[str] = Header(None, alias="X-Holon-Workspace-Id"),
) -> dict:
    """Register a new SQL database source."""
    target_workspace = _resolve_workspace(
        explicit=body.workspace_id,
        workspace_id=workspace_id,
        x_holon_workspace_id=x_holon_workspace_id,
    )
    await _authorize_workspace(principal, "write", workspace_id=target_workspace)
    existing = await _authorize_source_update(sql_source_registry, principal, body.name, target_workspace)
    try:
        registration = await sql_source_registry.register_source(
            deps.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            workspace_id=target_workspace,
            connection_name=body.connection_name,
            table_name=body.table_name,
            query=body.query,
            schedule_interval_minutes=body.schedule_interval_minutes,
            cursor_property=body.cursor_property,
            created_by_urn=principal.urn,
            reserved_dataset_names=await _reserved_dataset_names(deps.pool),
        )
    except sql_source_registry.SourceConflictError as exc:
        raise HolonError.conflict('SourceConflict', str(exc)) from exc
    except sql_source_registry.SourceConfigError as exc:
        raise HolonError.invalid_argument('SourceValidationFailed', str(exc)) from exc
    await _seed_source_authz(
        tenant_id=principal.tenant_id,
        workspace_id=target_workspace,
        name=body.name,
        compensate_delete=None
        if existing is not None
        else lambda: sql_source_registry.delete_source(deps.pool, principal.tenant_id, body.name),
    )
    emit_audit(
        category="access",
        action="connectivity.sql_source.registered",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="source",
        resource_urn=source_urn(principal.tenant_id, target_workspace, body.name),
        extra={"connection_name": body.connection_name},
    )
    return registration


@router.get("/sql-sources")
async def list_sql_sources(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await _filter_readable(
        principal, "source", await sql_source_registry.list_sources(deps.pool, principal.tenant_id)
    )


@router.post("/sql-sources/{name}/disable")
async def disable_sql_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    source = await sql_source_registry.get_source(deps.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    await _authorize_source(principal, "write", name=name, workspace_id=resource_workspace(source))
    return await sql_source_registry.set_source_status(deps.pool, principal.tenant_id, name, "disabled")


@router.post("/sql-sources/{name}/enable")
async def enable_sql_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    source = await sql_source_registry.get_source(deps.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    await _authorize_source(principal, "write", name=name, workspace_id=resource_workspace(source))
    return await sql_source_registry.set_source_status(deps.pool, principal.tenant_id, name, "active")


@router.delete("/sql-sources/{name}")
async def delete_sql_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    source = await sql_source_registry.get_source(deps.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    ws = resource_workspace(source)
    await _authorize_source(principal, "write", name=name, workspace_id=ws)
    await sql_source_registry.delete_source(deps.pool, principal.tenant_id, name)
    await _unlink_resource_authz("source", tenant_id=principal.tenant_id, workspace_id=ws, name=name)
    emit_audit(
        category="access",
        action="connectivity.sql_source.deleted",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="source",
        resource_urn=source_urn(principal.tenant_id, ws, name),
    )
    return {"deleted": name}


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
    existing = await _authorize_source_update(object_source_registry, principal, body.name, target_workspace)
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
    source = await object_source_registry.get_source(deps.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    await _authorize_source(principal, "write", name=name, workspace_id=resource_workspace(source))
    return await object_source_registry.set_source_status(deps.pool, principal.tenant_id, name, "disabled")


@router.post("/object-sources/{name}/enable")
async def enable_object_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    source = await object_source_registry.get_source(deps.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    await _authorize_source(principal, "write", name=name, workspace_id=resource_workspace(source))
    return await object_source_registry.set_source_status(deps.pool, principal.tenant_id, name, "active")


@router.delete("/object-sources/{name}")
async def delete_object_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    source = await object_source_registry.get_source(deps.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    ws = resource_workspace(source)
    await _authorize_source(principal, "write", name=name, workspace_id=ws)
    await object_source_registry.delete_source(deps.pool, principal.tenant_id, name)
    await _unlink_resource_authz("source", tenant_id=principal.tenant_id, workspace_id=ws, name=name)
    emit_audit(
        category="access",
        action="connectivity.object_source.deleted",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="source",
        resource_urn=source_urn(principal.tenant_id, ws, name),
    )
    return {"deleted": name}


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
    existing = await _authorize_source_update(sftp_source_registry, principal, body.name, target_workspace)
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
    source = await sftp_source_registry.get_source(deps.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    await _authorize_source(principal, "write", name=name, workspace_id=resource_workspace(source))
    return await sftp_source_registry.set_source_status(deps.pool, principal.tenant_id, name, "disabled")


@router.post("/sftp-sources/{name}/enable")
async def enable_sftp_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    source = await sftp_source_registry.get_source(deps.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    await _authorize_source(principal, "write", name=name, workspace_id=resource_workspace(source))
    return await sftp_source_registry.set_source_status(deps.pool, principal.tenant_id, name, "active")


@router.delete("/sftp-sources/{name}")
async def delete_sftp_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    source = await sftp_source_registry.get_source(deps.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    ws = resource_workspace(source)
    await _authorize_source(principal, "write", name=name, workspace_id=ws)
    await sftp_source_registry.delete_source(deps.pool, principal.tenant_id, name)
    await _unlink_resource_authz("source", tenant_id=principal.tenant_id, workspace_id=ws, name=name)
    emit_audit(
        category="access",
        action="connectivity.sftp_source.deleted",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="source",
        resource_urn=source_urn(principal.tenant_id, ws, name),
    )
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
    existing = await _authorize_source_update(generic_source_registry, principal, body.name, target_workspace)
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
    source = await generic_source_registry.get_source(deps.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    await _authorize_source(principal, "write", name=name, workspace_id=resource_workspace(source))
    return await generic_source_registry.set_source_status(deps.pool, principal.tenant_id, name, "disabled")


@router.post("/sources/{name}/enable")
async def enable_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    source = await generic_source_registry.get_source(deps.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    await _authorize_source(principal, "write", name=name, workspace_id=resource_workspace(source))
    return await generic_source_registry.set_source_status(deps.pool, principal.tenant_id, name, "active")


@router.delete("/sources/{name}")
async def delete_source(name: str, principal: Principal = Depends(current_principal)) -> dict:
    source = await generic_source_registry.get_source(deps.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    ws = resource_workspace(source)
    await _authorize_source(principal, "write", name=name, workspace_id=ws)
    await generic_source_registry.delete_source(deps.pool, principal.tenant_id, name)
    await _unlink_resource_authz("source", tenant_id=principal.tenant_id, workspace_id=ws, name=name)
    emit_audit(
        category="access",
        action="connectivity.source.deleted",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="source",
        resource_urn=source_urn(principal.tenant_id, ws, name),
    )
    return {"deleted": name}
