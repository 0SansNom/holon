"""Connectivity plugins routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from holon_common import HolonError, Principal, build_urn
from holon_common.audit import emit_audit

from .. import deps, plugin_registry
from ..deps import _authorize_workspace, _reserved_dataset_names, current_principal
from ..ingest import RegisterPluginRequest, SetPluginScheduleRequest, _plugin_not_found


router = APIRouter()


@router.get("/plugins")
async def list_plugins(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await plugin_registry.list_plugin_registrations(deps.pool, principal.tenant_id)



@router.post("/plugins")
async def register_plugin(body: RegisterPluginRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Register a connector plugin by entry point."""
    await _authorize_workspace(principal, "write")
    try:
        registration = await plugin_registry.register_plugin(
            deps.pool,
            entry_point=body.entry_point,
            tenant_id=principal.tenant_id,
            reserved_dataset_names=await _reserved_dataset_names(deps.pool),
        )
    except plugin_registry.PluginConflictError as exc:
        raise HolonError.conflict('PluginConflict', str(exc)) from exc
    emit_audit(
        category="access",
        action="connectivity.plugin.registered",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="connector_plugin",
        resource_urn=build_urn(principal.tenant_id, "global", "connector-plugin", registration["name"]),
        extra={"entry_point": body.entry_point},
    )
    return registration



@router.get("/plugins/{name}")
async def get_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "read")
    registration = await plugin_registry.get_plugin_registration(deps.pool, name, principal.tenant_id)
    if registration is None:
        raise _plugin_not_found(name)
    return registration



@router.post("/plugins/{name}/disable")
async def disable_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    registration = await plugin_registry.get_plugin_registration(deps.pool, name, principal.tenant_id)
    if registration is None:
        raise _plugin_not_found(name)
    if registration.get("tenant_id") != principal.tenant_id:
        raise _plugin_not_found(name)
    result = await plugin_registry.set_plugin_status(
        deps.pool, name, "disabled", tenant_id=principal.tenant_id
    )
    if result is None:
        raise _plugin_not_found(name)
    emit_audit(
        category="access",
        action="connectivity.plugin.disabled",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="connector_plugin",
        resource_urn=build_urn(principal.tenant_id, "global", "connector-plugin", name),
    )
    return result



@router.post("/plugins/{name}/enable")
async def enable_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    registration = await plugin_registry.get_plugin_registration(deps.pool, name, principal.tenant_id)
    if registration is None:
        raise _plugin_not_found(name)
    if registration.get("tenant_id") != principal.tenant_id:
        raise _plugin_not_found(name)
    result = await plugin_registry.set_plugin_status(
        deps.pool, name, "active", tenant_id=principal.tenant_id
    )
    if result is None:
        raise _plugin_not_found(name)
    emit_audit(
        category="access",
        action="connectivity.plugin.enabled",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="connector_plugin",
        resource_urn=build_urn(principal.tenant_id, "global", "connector-plugin", name),
    )
    return result



@router.post("/plugins/{name}/schedule")
async def set_plugin_schedule(name: str, body: SetPluginScheduleRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Set or clear background execution schedule for a plugin."""
    await _authorize_workspace(principal, "write")
    if body.schedule_interval_minutes is not None and body.schedule_interval_minutes <= 0:
        raise HolonError.invalid_argument(
            "InvalidSchedule", "schedule_interval_minutes must be a positive number of minutes"
        )
    registration = await plugin_registry.get_plugin_registration(deps.pool, name, principal.tenant_id)
    if registration is None or registration.get("tenant_id") != principal.tenant_id:
        raise _plugin_not_found(name)
    result = await plugin_registry.set_plugin_schedule(
        deps.pool, name, body.schedule_interval_minutes, tenant_id=principal.tenant_id
    )
    if result is None:
        raise _plugin_not_found(name)
    emit_audit(
        category="access",
        action="connectivity.plugin.schedule_updated",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="connector_plugin",
        resource_urn=build_urn(principal.tenant_id, "global", "connector-plugin", name),
        extra={"schedule_interval_minutes": body.schedule_interval_minutes},
    )
    return result

