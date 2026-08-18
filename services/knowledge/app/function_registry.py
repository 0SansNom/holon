"""Function plugin registry.

Registers and resolves computed logic functions used by derived properties and action side effects.
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
    load_entry_point,
    register as _shared_register,
    set_status,
)


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await _shared_ensure_schema(conn)


async def register_function_plugin(pool: asyncpg.Pool, *, entry_point: str) -> dict:
    async def _conflict_check(pool: asyncpg.Pool, manifest) -> Optional[str]:
        if not manifest.function_name:
            return "function plugin manifest must declare function_name"
        existing = await find_active_by_manifest_field(pool, "function", "function_name", manifest.function_name)
        if existing is not None and existing["name"] != manifest.name:
            return f"function name {manifest.function_name!r} is already claimed by active plugin {existing['name']!r}"
        return None

    return await _shared_register(pool, entry_point=entry_point, expected_plugin_type="function", conflict_check=_conflict_check)


async def get_function_plugin_registration(pool: asyncpg.Pool, name: str) -> Optional[dict]:
    return await get_registration(pool, name)


async def set_function_plugin_status(pool: asyncpg.Pool, name: str, status: str) -> Optional[dict]:
    return await set_status(pool, name, status)


async def list_active_function_plugins(pool: asyncpg.Pool) -> list[dict]:
    return await list_active_by_type(pool, "function")


async def find_active_function_by_name(pool: asyncpg.Pool, function_name: str) -> Optional[dict]:
    """The lookup `_resolve_one`/`_resolve_many`'s derived-property
    resolution and `actions.py`'s side-effect dispatch both need: given
    the `function_name` declared in `implements`/`ACTION_DEFINITIONS`,
    find the currently active plugin registration backing it.
    """
    return await find_active_by_manifest_field(pool, "function", "function_name", function_name)


def load_function_plugin(manifest: dict):
    return load_entry_point(manifest["entry_point"])


__all__ = [
    "ensure_schema",
    "register_function_plugin",
    "get_function_plugin_registration",
    "set_function_plugin_status",
    "list_active_function_plugins",
    "find_active_function_by_name",
    "load_function_plugin",
    "PluginConflictError",
]
