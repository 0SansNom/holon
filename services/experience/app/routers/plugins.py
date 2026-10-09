"""UI component plugin registration."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from holon_common import HolonError, Principal

from .. import deps, ui_component_registry
from ..deps import _authorize_workspace, current_principal

router = APIRouter()


class RegisterUiComponentPluginRequest(BaseModel):
    entry_point: str


@router.post("/ui-component-plugins")
async def register_ui_component_plugin(
    body: RegisterUiComponentPluginRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Registers a UI component plugin. See `ui_component_registry.py`'s
    module docstring for details.

    A plugin controls the iframe URL rendered in every dashboard that uses
    its component.  It is therefore workspace-curation, not an operation
    available to every authenticated user.
    """
    await _authorize_workspace(principal, "write")
    try:
        return await ui_component_registry.register_ui_component_plugin(deps.pool, entry_point=body.entry_point)
    except ui_component_registry.PluginConflictError as exc:
        raise HolonError.conflict('PluginConflict', str(exc)) from exc


def _ui_component_plugin_not_found(name: str) -> HolonError:
    return HolonError.not_found(
        "UiComponentPluginNotFound",
        f"no UI component plugin registered as {name!r}",
        name=name,
    )


@router.get("/ui-component-plugins/{name}")
async def get_ui_component_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "read")
    registration = await ui_component_registry.get_ui_component_registration(deps.pool, name)
    if registration is None:
        raise _ui_component_plugin_not_found(name)
    return registration


@router.post("/ui-component-plugins/{name}/disable")
async def disable_ui_component_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    registration = await ui_component_registry.get_ui_component_registration(deps.pool, name)
    if registration is None:
        raise _ui_component_plugin_not_found(name)
    return await ui_component_registry.set_ui_component_status(deps.pool, name, "disabled")


@router.post("/ui-component-plugins/{name}/enable")
async def enable_ui_component_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    registration = await ui_component_registry.get_ui_component_registration(deps.pool, name)
    if registration is None:
        raise _ui_component_plugin_not_found(name)
    return await ui_component_registry.set_ui_component_status(deps.pool, name, "active")
