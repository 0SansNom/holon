"""Plugin CRUD — execution-adapter/export-format/function plugins, plus
`/functions/{name}/invoke`. Lowest-coupling route group in the split
(only `core.pool`/`core.current_principal`, no other shared helper),
first extracted to calibrate the pattern for the rest.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from holon_common import HolonError, Principal
from pydantic import BaseModel

from .. import execution_adapter_registry, export_format_registry, function_registry, ontology
from .. import core

router = APIRouter()


async def _authorize_plugin(principal: Principal, workspace_id: str, permission: str) -> None:
    """Workspace `permission` required to curate plugin registrations."""
    decision = await core.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=ontology.workspace_urn(principal.tenant_id, workspace_id),
        permission=permission,
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


class RegisterExecutionAdapterPluginRequest(BaseModel):
    entry_point: str


@router.post("/execution-adapter-plugins")
async def register_execution_adapter_plugin(
    body: RegisterExecutionAdapterPluginRequest,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    await _authorize_plugin(principal, workspace_id, "write")
    try:
        return await execution_adapter_registry.register_execution_adapter_plugin(
            core.pool, entry_point=body.entry_point
        )
    except execution_adapter_registry.PluginConflictError as exc:
        raise HolonError.conflict('PluginConflict', str(exc)) from exc


@router.get("/execution-adapter-plugins/{name}")
async def get_execution_adapter_plugin(
    name: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    await _authorize_plugin(principal, workspace_id, "read")
    registration = await execution_adapter_registry.get_execution_adapter_registration(core.pool, name)
    if registration is None:
        raise HolonError.not_found('ExecutionAdapterPluginNotFound', f"no execution adapter plugin registered as {name!r}", name=name)
    return registration


@router.post("/execution-adapter-plugins/{name}/disable")
async def disable_execution_adapter_plugin(
    name: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    await _authorize_plugin(principal, workspace_id, "write")
    registration = await execution_adapter_registry.get_execution_adapter_registration(core.pool, name)
    if registration is None:
        raise HolonError.not_found('ExecutionAdapterPluginNotFound', f"no execution adapter plugin registered as {name!r}", name=name)
    return await execution_adapter_registry.set_execution_adapter_status(core.pool, name, "disabled")


@router.post("/execution-adapter-plugins/{name}/enable")
async def enable_execution_adapter_plugin(
    name: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    await _authorize_plugin(principal, workspace_id, "write")
    registration = await execution_adapter_registry.get_execution_adapter_registration(core.pool, name)
    if registration is None:
        raise HolonError.not_found('ExecutionAdapterPluginNotFound', f"no execution adapter plugin registered as {name!r}", name=name)
    return await execution_adapter_registry.set_execution_adapter_status(core.pool, name, "active")


class RegisterExportFormatPluginRequest(BaseModel):
    entry_point: str


@router.post("/export-format-plugins")
async def register_export_format_plugin(
    body: RegisterExportFormatPluginRequest,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    await _authorize_plugin(principal, workspace_id, "write")
    try:
        return await export_format_registry.register_export_format_plugin(core.pool, entry_point=body.entry_point)
    except export_format_registry.PluginConflictError as exc:
        raise HolonError.conflict('PluginConflict', str(exc)) from exc


@router.get("/export-format-plugins/{name}")
async def get_export_format_plugin(
    name: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    await _authorize_plugin(principal, workspace_id, "read")
    registration = await export_format_registry.get_export_format_registration(core.pool, name)
    if registration is None:
        raise HolonError.not_found('ExportFormatPluginNotFound', f"no export format plugin registered as {name!r}", name=name)
    return registration


@router.post("/export-format-plugins/{name}/disable")
async def disable_export_format_plugin(
    name: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    await _authorize_plugin(principal, workspace_id, "write")
    registration = await export_format_registry.get_export_format_registration(core.pool, name)
    if registration is None:
        raise HolonError.not_found('ExportFormatPluginNotFound', f"no export format plugin registered as {name!r}", name=name)
    return await export_format_registry.set_export_format_status(core.pool, name, "disabled")


@router.post("/export-format-plugins/{name}/enable")
async def enable_export_format_plugin(
    name: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    await _authorize_plugin(principal, workspace_id, "write")
    registration = await export_format_registry.get_export_format_registration(core.pool, name)
    if registration is None:
        raise HolonError.not_found('ExportFormatPluginNotFound', f"no export format plugin registered as {name!r}", name=name)
    return await export_format_registry.set_export_format_status(core.pool, name, "active")


class RegisterFunctionPluginRequest(BaseModel):
    entry_point: str


@router.post("/function-plugins")
async def register_function_plugin(
    body: RegisterFunctionPluginRequest,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    await _authorize_plugin(principal, workspace_id, "write")
    try:
        return await function_registry.register_function_plugin(core.pool, entry_point=body.entry_point)
    except function_registry.PluginConflictError as exc:
        raise HolonError.conflict('PluginConflict', str(exc)) from exc


@router.get("/function-plugins/{name}")
async def get_function_plugin(
    name: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    await _authorize_plugin(principal, workspace_id, "read")
    registration = await function_registry.get_function_plugin_registration(core.pool, name)
    if registration is None:
        raise HolonError.not_found('FunctionPluginNotFound', f"no function plugin registered as {name!r}", name=name)
    return registration


@router.post("/function-plugins/{name}/disable")
async def disable_function_plugin(
    name: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    await _authorize_plugin(principal, workspace_id, "write")
    registration = await function_registry.get_function_plugin_registration(core.pool, name)
    if registration is None:
        raise HolonError.not_found('FunctionPluginNotFound', f"no function plugin registered as {name!r}", name=name)
    return await function_registry.set_function_plugin_status(core.pool, name, "disabled")


@router.post("/function-plugins/{name}/enable")
async def enable_function_plugin(
    name: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    await _authorize_plugin(principal, workspace_id, "write")
    registration = await function_registry.get_function_plugin_registration(core.pool, name)
    if registration is None:
        raise HolonError.not_found('FunctionPluginNotFound', f"no function plugin registered as {name!r}", name=name)
    return await function_registry.set_function_plugin_status(core.pool, name, "active")


class InvokeFunctionRequest(BaseModel):
    rows: list[dict]


@router.post("/functions/{name}/invoke")
async def invoke_function(
    name: str, request: InvokeFunctionRequest, principal=Depends(core.current_principal)
) -> dict:
    """The third Function call site (Connectivity's Pipeline
    TransformSteps, `services/connectivity/app/pipeline.py`), alongside
    read-time derived properties (`_apply_derived_properties`) and Action
    side effects (`actions._invoke_function_side_effect`) — each call
    site has always had its own output contract, this is simply the
    third. Here the Function is a row -> row map over an entire Iceberg
    table: the plugin's return value *is* the output row in full, not a
    partial dict merged into an existing ObjectType instance the way a
    derived property is. Authenticated-only, no workspace-tier gate: a
    Function carries no data of its own to protect — whatever
    authorization applies to the actual dataset rows is the caller's
    concern, the same trust boundary Connectivity's own `/sync` already
    has for cataloguing an arbitrary dataset.
    """
    registration = await function_registry.find_active_function_by_name(core.pool, name)
    if registration is None:
        raise HolonError.not_found('FunctionPluginNotFound', f"no active function plugin registered for {name!r}", name=name)
    plugin = function_registry.load_function_plugin(registration["manifest"])
    output_rows = []
    for row in request.rows:
        output = await plugin.call(**row)
        output_rows.append(output if isinstance(output, dict) else row)
    return {"rows": output_rows}
