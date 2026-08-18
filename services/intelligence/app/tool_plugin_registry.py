"""External agent tool plugin registry.

Registers and resolves synthetic agent tools exposed to the Agent Runtime.
"""

from __future__ import annotations

from typing import Optional

import asyncpg
import httpx
from .knowledge_urls import holon_url

from holon_common.plugin import (
    PluginConflictError,
    ensure_schema as _shared_ensure_schema,
    find_active_by_manifest_field,
    get_registration,
    list_active_by_type,
    load_entry_point,
    register as _shared_register,
    set_status,
)

from .tool_plugin_entry import assert_entry_point_allowed


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await _shared_ensure_schema(conn)


async def register_tool_plugin(
    pool: asyncpg.Pool, http: httpx.AsyncClient, *, entry_point: str, knowledge_url: str, headers: dict
) -> dict:
    assert_entry_point_allowed(entry_point)
    # Real Knowledge Action tool names, computed the identical way
    # `agent_runtime._list_tools` does — a plugin can't register a tool
    # name that would collide with (and, to the model, be indistinguishable
    # from) a genuine, audited ontology Action.
    response = await http.get(holon_url(knowledge_url, "/actions"), headers=headers)
    response.raise_for_status()
    knowledge_tool_names = {action["name"].replace(".", "_") for action in response.json()}

    async def _conflict_check(pool: asyncpg.Pool, manifest) -> Optional[str]:
        if not manifest.tool_name or not manifest.tool_description or not manifest.input_schema:
            return "agent_tool plugin manifest must declare tool_name, tool_description, and input_schema"
        if manifest.tool_name in knowledge_tool_names:
            return f"tool name {manifest.tool_name!r} collides with a real Knowledge Action"
        existing = await find_active_by_manifest_field(pool, "agent_tool", "tool_name", manifest.tool_name)
        if existing is not None and existing["name"] != manifest.name:
            return f"tool name {manifest.tool_name!r} is already claimed by active plugin {existing['name']!r}"
        return None

    return await _shared_register(pool, entry_point=entry_point, expected_plugin_type="agent_tool", conflict_check=_conflict_check)


async def get_tool_plugin_registration(pool: asyncpg.Pool, name: str) -> Optional[dict]:
    return await get_registration(pool, name)


async def set_tool_plugin_status(pool: asyncpg.Pool, name: str, status: str) -> Optional[dict]:
    return await set_status(pool, name, status)


async def list_active_tool_plugins(pool: asyncpg.Pool) -> list[dict]:
    """Called from `agent_runtime._list_tools()` every turn — recomputed fresh
    so disabling a plugin takes effect on the very next turn.
    """
    return await list_active_by_type(pool, "agent_tool")


def load_tool_plugin(manifest: dict):
    return load_entry_point(manifest["entry_point"])


__all__ = [
    "ensure_schema",
    "register_tool_plugin",
    "get_tool_plugin_registration",
    "set_tool_plugin_status",
    "list_active_tool_plugins",
    "load_tool_plugin",
    "PluginConflictError",
]
