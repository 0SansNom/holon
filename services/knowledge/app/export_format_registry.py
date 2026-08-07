"""The **export format** plugin type. See `holon_common.plugin`'s module docstring for
how this fits alongside the other plugin extension points.

Registers a new output serialization for `GET /objects/{type}/export`
beyond the plain JSON every other read endpoint already returns — the
example plugin (`plugins/csv_export_plugin.py`) adds CSV, using nothing
but the stdlib `csv` module. Reads always go through the same
already-permission-gated, already-masked path as every other object
read (`_resolve_many`, confidential property masking included) — export is a
*serialization* concern only, it never bypasses ABAC/ReBAC to reach more
data than the caller could already see through the ordinary JSON
endpoint.

Enforced format-name-ownership guard: a plugin can't
register itself as `json` (the built-in, always-available format) or a
name another active export-format plugin already owns.

Isolation ("sandbox"): `serialize()` is application code in this same process;
it only ever receives already-fetched, already-masked rows, never a
database connection of its own.
"""

from __future__ import annotations

from typing import Optional

import asyncpg

from holon_common.plugin import (
    PluginConflictError,
    ensure_schema as _shared_ensure_schema,
    find_active_by_manifest_field,
    get_registration,
    load_entry_point,
    register as _shared_register,
    set_status,
)

BUILTIN_FORMAT_NAMES = {"json"}


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await _shared_ensure_schema(conn)


async def register_export_format_plugin(pool: asyncpg.Pool, *, entry_point: str) -> dict:
    async def _conflict_check(pool: asyncpg.Pool, manifest) -> Optional[str]:
        if not manifest.format_name or not manifest.content_type:
            return "export_format plugin manifest must declare format_name and content_type"
        if manifest.format_name in BUILTIN_FORMAT_NAMES:
            return f"format name {manifest.format_name!r} collides with the built-in format"
        existing = await find_active_by_manifest_field(pool, "export_format", "format_name", manifest.format_name)
        if existing is not None and existing["name"] != manifest.name:
            return f"format name {manifest.format_name!r} is already claimed by active plugin {existing['name']!r}"
        return None

    return await _shared_register(
        pool, entry_point=entry_point, expected_plugin_type="export_format", conflict_check=_conflict_check
    )


async def get_export_format_registration(pool: asyncpg.Pool, name: str) -> Optional[dict]:
    return await get_registration(pool, name)


async def set_export_format_status(pool: asyncpg.Pool, name: str, status: str) -> Optional[dict]:
    return await set_status(pool, name, status)


async def find_active_format(pool: asyncpg.Pool, format_name: str):
    """Returns `(loaded plugin instance, content_type)`, or `None` if
    `format_name` isn't a registered active plugin (the caller decides
    what to do about `json`, the always-available built-in, itself).
    """
    registration = await find_active_by_manifest_field(pool, "export_format", "format_name", format_name)
    if registration is None:
        return None
    return load_entry_point(registration["manifest"]["entry_point"]), registration["manifest"]["content_type"]


__all__ = [
    "BUILTIN_FORMAT_NAMES",
    "ensure_schema",
    "register_export_format_plugin",
    "get_export_format_registration",
    "set_export_format_status",
    "find_active_format",
    "PluginConflictError",
]
