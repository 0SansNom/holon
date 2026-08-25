"""Execution adapter plugin registry.

Registers and resolves custom execution engine adapters for ObjectTypes.
"""

from __future__ import annotations

from typing import Optional

import asyncpg

from holon_common.plugin import (
    PluginConflictError,
    find_active_by_manifest_field,
    get_registration,
    load_entry_point,
    register as _shared_register,
    set_status,
)


async def register_execution_adapter_plugin(pool: asyncpg.Pool, *, entry_point: str) -> dict:
    async def _conflict_check(pool: asyncpg.Pool, manifest) -> Optional[str]:
        if not manifest.adapter_object_type:
            return "execution_adapter plugin manifest must declare adapter_object_type"
        existing = await find_active_by_manifest_field(
            pool, "execution_adapter", "adapter_object_type", manifest.adapter_object_type
        )
        if existing is not None and existing["name"] != manifest.name:
            return (
                f"ObjectType {manifest.adapter_object_type!r} already has an active adapter "
                f"{existing['name']!r} registered"
            )
        return None

    return await _shared_register(
        pool, entry_point=entry_point, expected_plugin_type="execution_adapter", conflict_check=_conflict_check
    )


async def get_execution_adapter_registration(pool: asyncpg.Pool, name: str) -> Optional[dict]:
    return await get_registration(pool, name)


async def set_execution_adapter_status(pool: asyncpg.Pool, name: str, status: str) -> Optional[dict]:
    return await set_status(pool, name, status)


async def find_active_adapter_for_object_type(pool: asyncpg.Pool, object_type: str):
    """Returns a loaded, ready-to-call adapter instance, or `None` if the
    ObjectType has no active adapter override — the caller (`execution.py`)
    falls back to the built-in DuckDB adapter in that case.
    """
    registration = await find_active_by_manifest_field(pool, "execution_adapter", "adapter_object_type", object_type)
    if registration is None:
        return None
    return load_entry_point(registration["manifest"]["entry_point"])


__all__ = [
    "register_execution_adapter_plugin",
    "get_execution_adapter_registration",
    "set_execution_adapter_status",
    "find_active_adapter_for_object_type",
    "PluginConflictError",
]
