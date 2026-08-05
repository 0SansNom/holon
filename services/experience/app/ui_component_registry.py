"""The **UI component** plugin type. See `holon_common.plugin`'s module docstring
for details.

A UI component plugin declares a new bindable component type beyond the
three built into Application Builder's own object app/dashboard surfaces
(`table`, `detail`, `kpi`). `application_builder.py`'s binding/widget
validation is extended to accept a plugin-registered `component_name` in
addition to the built-ins.

Component-name-ownership guard: a plugin can't register itself under
`table`/`detail`/`kpi` (the built-ins) or a name another active
ui_component plugin already owns.
"""

from __future__ import annotations

from typing import Optional

import asyncpg

from holon_common.plugin import (
    PluginConflictError,
    ensure_schema as _shared_ensure_schema,
    find_active_by_manifest_field,
    get_registration,
    list_active_by_type,
    register as _shared_register,
    set_status,
)

BUILTIN_COMPONENT_NAMES = {"table", "detail", "kpi"}


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await _shared_ensure_schema(conn)


async def register_ui_component_plugin(pool: asyncpg.Pool, *, entry_point: str) -> dict:
    async def _conflict_check(pool: asyncpg.Pool, manifest) -> Optional[str]:
        if not manifest.component_name or not manifest.iframe_url:
            return "ui_component plugin manifest must declare component_name and iframe_url"
        if manifest.component_name in BUILTIN_COMPONENT_NAMES:
            return f"component name {manifest.component_name!r} collides with a built-in component"
        existing = await find_active_by_manifest_field(pool, "ui_component", "component_name", manifest.component_name)
        if existing is not None and existing["name"] != manifest.name:
            return f"component name {manifest.component_name!r} is already claimed by active plugin {existing['name']!r}"
        return None

    return await _shared_register(pool, entry_point=entry_point, expected_plugin_type="ui_component", conflict_check=_conflict_check)


async def get_ui_component_registration(pool: asyncpg.Pool, name: str) -> Optional[dict]:
    return await get_registration(pool, name)


async def set_ui_component_status(pool: asyncpg.Pool, name: str, status: str) -> Optional[dict]:
    return await set_status(pool, name, status)


async def is_valid_component_name(pool: asyncpg.Pool, component_name: str) -> bool:
    if component_name in BUILTIN_COMPONENT_NAMES:
        return True
    registration = await find_active_by_manifest_field(pool, "ui_component", "component_name", component_name)
    return registration is not None


async def get_component_registration_by_name(pool: asyncpg.Pool, component_name: str) -> Optional[dict]:
    if component_name in BUILTIN_COMPONENT_NAMES:
        return None
    return await find_active_by_manifest_field(pool, "ui_component", "component_name", component_name)


async def list_active_ui_component_plugins(pool: asyncpg.Pool) -> list[dict]:
    return await list_active_by_type(pool, "ui_component")


__all__ = [
    "BUILTIN_COMPONENT_NAMES",
    "ensure_schema",
    "register_ui_component_plugin",
    "get_ui_component_registration",
    "set_ui_component_status",
    "is_valid_component_name",
    "get_component_registration_by_name",
    "list_active_ui_component_plugins",
    "PluginConflictError",
]
