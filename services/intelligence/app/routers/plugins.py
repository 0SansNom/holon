"""Agent tool plugin registration."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from holon_common import HolonError, Principal
from holon_common.audit import emit_audit

from .. import deps, tool_plugin_registry
from ..deps import (
    KNOWLEDGE_URL,
    _authorize_tool_plugin,
    _authorize_workspace,
    _seed_tool_plugin_authz,
    allow_tool_plugin_register,
    current_principal,
    require_intelligence_enabled,
    tool_plugin_not_found,
    tool_plugin_urn,
)

router = APIRouter()

class RegisterToolPluginRequest(BaseModel):
    entry_point: str


@router.post("/tool-plugins")
async def register_tool_plugin(
    body: RegisterToolPluginRequest, http_request: Request, principal: Principal = Depends(current_principal)
) -> dict:
    """Register an external agent tool plugin."""
    require_intelligence_enabled()
    if not allow_tool_plugin_register():
        raise HolonError.forbidden('PrincipalDisabled', "tool plugin registration disabled "
            "(HOLON_ALLOW_TOOL_PLUGIN_REGISTER — refused in production posture)",)
    await _authorize_workspace(principal, "write")
    authorization = http_request.headers.get("authorization", "")
    async with httpx.AsyncClient(timeout=15.0) as http:
        try:
            registration = await tool_plugin_registry.register_tool_plugin(
                deps.pool, http, entry_point=body.entry_point, knowledge_url=KNOWLEDGE_URL,
                headers={"Authorization": authorization},
            )
        except ValueError as exc:
            raise HolonError.invalid_argument('PluginValidationFailed', str(exc)) from exc
        except tool_plugin_registry.PluginConflictError as exc:
            raise HolonError.conflict('PluginConflict', str(exc)) from exc

    name = registration["name"]

    async def _compensate():
        await deps.pool.execute("DELETE FROM plugin_registration WHERE name = $1", name)

    await _seed_tool_plugin_authz(
        tenant_id=principal.tenant_id, name=name, compensate_delete=_compensate
    )
    emit_audit(
        category="access",
        action="intelligence.tool_plugin.registered",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="tool_plugin",
        resource_urn=tool_plugin_urn(principal.tenant_id, name),
        extra={"entry_point": body.entry_point},
    )
    return registration



@router.get("/tool-plugins/{name}")
async def get_tool_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    registration = await tool_plugin_registry.get_tool_plugin_registration(deps.pool, name)
    if registration is None:
        raise tool_plugin_not_found(name)
    await _authorize_tool_plugin(principal, "read", name=name)
    return registration


@router.post("/tool-plugins/{name}/disable")
async def disable_tool_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    require_intelligence_enabled()
    registration = await tool_plugin_registry.get_tool_plugin_registration(deps.pool, name)
    if registration is None:
        raise tool_plugin_not_found(name)
    await _authorize_tool_plugin(principal, "write", name=name)
    return await tool_plugin_registry.set_tool_plugin_status(deps.pool, name, "disabled")


@router.post("/tool-plugins/{name}/enable")
async def enable_tool_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    require_intelligence_enabled()
    registration = await tool_plugin_registry.get_tool_plugin_registration(deps.pool, name)
    if registration is None:
        raise tool_plugin_not_found(name)
    await _authorize_tool_plugin(principal, "write", name=name)
    return await tool_plugin_registry.set_tool_plugin_status(deps.pool, name, "active")


