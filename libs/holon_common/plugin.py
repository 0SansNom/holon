"""Plugin SDK. Six extension-point types are supported:
Connectors, Agent tools, UI components, Execution adapters, Export formats, Functions.

`PluginManifest` is one shared shape: every plugin type declares common
metadata (name, version, capabilities, permissions, events, checksum via
`entry_point`).
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import logging
import os
from typing import Any, Literal, Optional, Protocol

import asyncpg
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger("holon_common.plugin")

PluginType = Literal["connector", "agent_tool", "ui_component", "execution_adapter", "export_format", "function"]


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    plugin_type: PluginType
    capabilities: dict[str, Any] = {}
    permissions_required: list[str] = []
    events_consumed: list[str] = []
    events_published: list[str] = []
    entry_point: str  # "module.path:ClassName" — dynamically imported, never hand-wired into dispatch code

    # Connector-specific (services/connectivity/app/plugin_registry.py)
    dataset_name: Optional[str] = None
    # Stable connector URN local-name (e.g. postgres-source-erp). When unset,
    # Connectivity falls back to `plugin-{manifest.name}`.
    connector_local_name: Optional[str] = None

    # Agent-tool-specific (services/intelligence/app/tool_plugin_registry.py)
    tool_name: Optional[str] = None
    tool_description: Optional[str] = None
    input_schema: Optional[dict[str, Any]] = None
    risk_level: Optional[Literal["low", "high"]] = None

    # UI-component-specific (services/experience/app/ui_component_registry.py)
    component_name: Optional[str] = None
    binding_contract: Optional[dict[str, Any]] = None
    iframe_url: Optional[str] = None

    # Execution-adapter-specific (services/knowledge/app/execution_adapter_registry.py)
    adapter_object_type: Optional[str] = None

    # Export-format-specific (services/knowledge/app/export_format_registry.py)
    format_name: Optional[str] = None
    content_type: Optional[str] = None

    # Function-specific (services/knowledge/app/function_registry.py).
    # Reuses `input_schema` above (already shared with agent-tool) for the
    # expected kwargs shape rather than declaring a second field for the
    # same concept.
    function_name: Optional[str] = None


class ConnectorPlugin(Protocol):
    manifest: PluginManifest

    async def fetch(self) -> list[dict]: ...


class AgentToolPlugin(Protocol):
    manifest: PluginManifest

    async def invoke(self, tool_input: dict) -> dict: ...


class ExecutionAdapterPlugin(Protocol):
    manifest: PluginManifest

    async def execute(
        self, pool: asyncpg.Pool, *, object_type: str, tenant_id: str, filter_property: str, filter_value: str, operation: str
    ) -> Any: ...


class ExportFormatPlugin(Protocol):
    manifest: PluginManifest

    def serialize(self, rows: list[dict]) -> bytes: ...


class FunctionPlugin(Protocol):
    manifest: PluginManifest

    async def call(self, **kwargs: Any) -> Any: ...


DDL = """
CREATE TABLE IF NOT EXISTS plugin_registration (
    name TEXT PRIMARY KEY,
    plugin_type TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest JSONB NOT NULL,
    checksum TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


class PluginConflictError(ValueError):
    pass


_DEFAULT_ENTRY_PREFIXES = ("app.plugins.", "app.tool_plugins.")


def entry_prefixes() -> tuple[str, ...]:
    raw = (
        os.environ.get("HOLON_PLUGIN_ENTRY_PREFIXES")
        or os.environ.get("HOLON_TOOL_PLUGIN_ENTRY_PREFIXES")
        or ""
    ).strip()
    if not raw:
        return _DEFAULT_ENTRY_PREFIXES
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def assert_entry_point_allowed(entry_point: str) -> None:
    prefixes = entry_prefixes()
    if not any(entry_point.startswith(p) for p in prefixes):
        raise PluginConflictError(
            f"plugin entry_point {entry_point!r} not under allowed prefixes {prefixes} "
            "(HOLON_PLUGIN_ENTRY_PREFIXES)"
        )


def load_entry_point(entry_point: str) -> Any:
    """`invalidate_caches()` matters here specifically because plugins are
    meant to be written to disk and registered in the same breath (that's
    the whole point of this SDK) — without it, a module written within
    the same filesystem mtime tick as the last import can silently miss
    `FileFinder`'s directory-listing cache and raise a spurious
    `ModuleNotFoundError`, a well-known stdlib gotcha for dynamically
    created modules (see `importlib.invalidate_caches`'s own docs).
    """
    assert_entry_point_allowed(entry_point)
    importlib.invalidate_caches()
    module_path, class_name = entry_point.split(":")
    module = importlib.import_module(module_path)
    return getattr(module, class_name)()


def checksum_of(entry_point: str) -> str:
    assert_entry_point_allowed(entry_point)
    importlib.invalidate_caches()
    module_path, _ = entry_point.split(":")
    module = importlib.import_module(module_path)
    source = inspect.getsource(module)
    return hashlib.sha256(source.encode()).hexdigest()


def _parse_row(row: asyncpg.Record) -> dict:
    result = dict(row)
    if isinstance(result["manifest"], str):
        result["manifest"] = json.loads(result["manifest"])
    return result


async def get_registration(pool: asyncpg.Pool, name: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM plugin_registration WHERE name = $1", name)
    return None if row is None else _parse_row(row)


async def register(
    pool: asyncpg.Pool,
    *,
    entry_point: str,
    expected_plugin_type: PluginType,
    conflict_check: Optional[Any] = None,
) -> dict:
    """Generic registration shared by all five plugin types. `conflict_check`
    is an optional `async def(pool, manifest) -> Optional[str]` — returns
    a conflict reason to reject with, or `None` to proceed.
    """
    plugin = load_entry_point(entry_point)
    manifest = plugin.manifest

    if manifest.plugin_type != expected_plugin_type:
        raise PluginConflictError(
            f"entry point {entry_point!r} declares plugin_type {manifest.plugin_type!r}, "
            f"expected {expected_plugin_type!r}"
        )

    if conflict_check is not None:
        conflict = await conflict_check(pool, manifest)
        if conflict is not None:
            raise PluginConflictError(conflict)

    checksum = checksum_of(entry_point)
    await pool.execute(
        """
        INSERT INTO plugin_registration (name, plugin_type, version, manifest, checksum, status)
        VALUES ($1, $2, $3, $4::jsonb, $5, 'active')
        ON CONFLICT (name) DO UPDATE SET
            plugin_type = EXCLUDED.plugin_type, version = EXCLUDED.version, manifest = EXCLUDED.manifest,
            checksum = EXCLUDED.checksum, status = 'active'
        """,
        manifest.name, manifest.plugin_type, manifest.version, manifest.model_dump_json(), checksum,
    )
    return await get_registration(pool, manifest.name)


async def set_status(pool: asyncpg.Pool, name: str, status: str) -> Optional[dict]:
    """Activatable/deactivatable without redeploy."""
    await pool.execute("UPDATE plugin_registration SET status = $1 WHERE name = $2", status, name)
    return await get_registration(pool, name)


async def list_active_by_type(pool: asyncpg.Pool, plugin_type: PluginType) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM plugin_registration WHERE plugin_type = $1 AND status = 'active'", plugin_type
    )
    return [_parse_row(row) for row in rows]


async def find_active_by_manifest_field(pool: asyncpg.Pool, plugin_type: PluginType, field: str, value: str) -> Optional[dict]:
    row = await pool.fetchrow(
        "SELECT * FROM plugin_registration WHERE plugin_type = $1 AND manifest->>$2 = $3 AND status = 'active'",
        plugin_type, field, value,
    )
    return None if row is None else _parse_row(row)
