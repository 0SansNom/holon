"""The Connector plugin registry. See `holon_common.plugin`'s module docstring
for details.

Ecosystem connectors are executed without modifying core dispatch logic:
`load_active_plugin_for_dataset` dynamically imports whatever `entry_point` the
manifest declares. `main.py`'s `run_sync` dispatch consults this registry first,
then falls through to the generic REST source registry.

Dataset-ownership guard: A plugin cannot register itself against a reserved
stream name (`inventory_levels`), or against a dataset already claimed by
another active plugin in the same tenant scope (both NULL = global, or equal
`tenant_id`).
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import logging
from typing import Optional

import asyncpg

from holon_common.plugin import ConnectorPlugin

logger = logging.getLogger("connectivity.plugin_registry")

DEFAULT_RESERVED_DATASET_NAMES = frozenset({"inventory_levels"})

DDL = """
CREATE TABLE IF NOT EXISTS plugin_registration (
    name TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    manifest JSONB NOT NULL,
    checksum TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- NULL tenant_id = global plugin (e.g. exchange-rate); non-null = tenant-scoped.
ALTER TABLE plugin_registration ADD COLUMN IF NOT EXISTS tenant_id TEXT;

-- Same scheduling model as `generic_rest_source` — NULL = manual only.
ALTER TABLE plugin_registration ADD COLUMN IF NOT EXISTS schedule_interval_minutes INTEGER;
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


class PluginConflictError(ValueError):
    pass


def _load_entry_point(entry_point: str) -> ConnectorPlugin:
    # Same FileFinder mtime-cache gotcha as holon_common.plugin.load_entry_point:
    # tests (and operators) write plugin modules then register them immediately.
    importlib.invalidate_caches()
    module_path, class_name = entry_point.split(":")
    module = importlib.import_module(module_path)
    return getattr(module, class_name)()


def _checksum_of(entry_point: str) -> str:
    importlib.invalidate_caches()
    module_path, _ = entry_point.split(":")
    module = importlib.import_module(module_path)
    source = inspect.getsource(module)
    return hashlib.sha256(source.encode()).hexdigest()


def _parse_manifest(row: asyncpg.Record) -> dict:
    result = dict(row)
    if isinstance(result["manifest"], str):
        result["manifest"] = json.loads(result["manifest"])
    return result


async def get_plugin_registration(pool: asyncpg.Pool, name: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM plugin_registration WHERE name = $1", name)
    return None if row is None else _parse_manifest(row)


async def list_plugin_registrations(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    """Connector plugins visible to `tenant_id` — global (NULL) plus this
    tenant's own. Same visibility rule as `load_active_plugin_for_dataset`,
    surfaced for the Data Sources UI (`GET /sources` counterpart).
    """
    rows = await pool.fetch(
        "SELECT * FROM plugin_registration WHERE tenant_id IS NULL OR tenant_id = $1 ORDER BY name",
        tenant_id,
    )
    return [_parse_manifest(row) for row in rows]


async def register_plugin(
    pool: asyncpg.Pool,
    *,
    entry_point: str,
    tenant_id: Optional[str] = None,
    reserved_dataset_names: frozenset[str] = DEFAULT_RESERVED_DATASET_NAMES,
) -> dict:
    plugin = _load_entry_point(entry_point)
    manifest = plugin.manifest

    if manifest.dataset_name in reserved_dataset_names:
        raise PluginConflictError(f"dataset {manifest.dataset_name!r} is reserved")

    conflicting = await pool.fetchrow(
        """
        SELECT name FROM plugin_registration
        WHERE manifest->>'dataset_name' = $1
          AND status = 'active'
          AND name != $2
          AND (
            (tenant_id IS NULL AND $3::text IS NULL)
            OR tenant_id IS NOT DISTINCT FROM $3
          )
        """,
        manifest.dataset_name,
        manifest.name,
        tenant_id,
    )
    if conflicting is not None:
        raise PluginConflictError(
            f"dataset {manifest.dataset_name!r} is already claimed by active plugin {conflicting['name']!r}"
        )

    checksum = _checksum_of(entry_point)
    await pool.execute(
        """
        INSERT INTO plugin_registration (name, version, manifest, checksum, status, tenant_id)
        VALUES ($1, $2, $3::jsonb, $4, 'active', $5)
        ON CONFLICT (name) DO UPDATE SET
            version = EXCLUDED.version, manifest = EXCLUDED.manifest,
            checksum = EXCLUDED.checksum, status = 'active',
            tenant_id = EXCLUDED.tenant_id
        """,
        manifest.name,
        manifest.version,
        manifest.model_dump_json(),
        checksum,
        tenant_id,
    )
    return await get_plugin_registration(pool, manifest.name)


async def set_plugin_status(pool: asyncpg.Pool, name: str, status: str) -> Optional[dict]:
    """Activatable/deactivatable without redeploy: flips a column
    the dispatch path (`load_active_plugin_for_dataset`) actually checks.
    """
    await pool.execute("UPDATE plugin_registration SET status = $1 WHERE name = $2", status, name)
    return await get_plugin_registration(pool, name)


async def set_plugin_schedule(pool: asyncpg.Pool, name: str, schedule_interval_minutes: Optional[int]) -> Optional[dict]:
    await pool.execute(
        "UPDATE plugin_registration SET schedule_interval_minutes = $1 WHERE name = $2",
        schedule_interval_minutes, name,
    )
    return await get_plugin_registration(pool, name)


async def list_all_scheduled_plugins(pool: asyncpg.Pool) -> list[dict]:
    """Due-check feed for `main.py`'s scheduler loop — the plugin
    counterpart of `generic_source_registry.list_all_scheduled_sources`.
    Global (NULL tenant_id) plugins are excluded: nothing in this build
    ever registers one that way (`POST /plugins` always stamps the
    caller's own `tenant_id`), and there's no well-defined tenant to run
    a scheduled sync under otherwise.
    """
    rows = await pool.fetch(
        """
        SELECT tenant_id, manifest->>'dataset_name' AS dataset_name, schedule_interval_minutes
        FROM plugin_registration
        WHERE status = 'active' AND schedule_interval_minutes IS NOT NULL AND tenant_id IS NOT NULL
        """
    )
    return [dict(row) for row in rows]


async def load_active_plugin_for_dataset(
    pool: asyncpg.Pool, dataset_name: str, tenant_id: str
) -> Optional[ConnectorPlugin]:
    """Load an active plugin for `dataset_name` visible to `tenant_id`.

    Matches global plugins (`tenant_id IS NULL`) and plugins scoped to this
    tenant. Prefer tenant-specific over global when both exist.
    """
    row = await pool.fetchrow(
        """
        SELECT manifest FROM plugin_registration
        WHERE manifest->>'dataset_name' = $1
          AND status = 'active'
          AND (tenant_id IS NULL OR tenant_id = $2)
        ORDER BY tenant_id NULLS LAST
        LIMIT 1
        """,
        dataset_name,
        tenant_id,
    )
    if row is None:
        return None
    manifest_dict = json.loads(row["manifest"]) if isinstance(row["manifest"], str) else row["manifest"]
    return _load_entry_point(manifest_dict["entry_point"])
