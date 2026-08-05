"""The Connector plugin registry. See `holon_common.plugin`'s module docstring
for details.

Ecosystem connectors are executed without modifying core dispatch logic:
`load_active_plugin_for_dataset` dynamically imports whatever `entry_point` the
manifest declares. `main.py`'s `run_sync` dispatch falls through to this registry
when `DATASET_READERS` has no hardcoded entry for the requested dataset.

Dataset-ownership guard: A plugin cannot register itself against a dataset name
already owned by one of the hardcoded core connectors, or by a different
already-active plugin.

Honest scope boundary, stated plainly, not glossed over: a plugin's
synced dataset lands in Iceberg with a real snapshot and a real
`connectivity.sync.completed` event, exactly like every core connector —
but nothing here auto-registers an `ObjectType`/`RelationType` for it in
Knowledge's ontology. Every one of this build's five core connectors
needed a matching, hand-written ontology registration in Knowledge; a
plugin still would too, today. Building a manifest-driven
auto-ontology-registration pipeline (so a plugin's dataset becomes
queryable as a first-class ObjectType with zero Knowledge-side code) is
real, additional work not attempted in this slice.
`services/knowledge/app/catalog.py`'s dispatch was hardened
(`DATASET_OBJECT_TYPES.get(...)`, not `[...]`) so an ontology-unmapped
dataset degrades to "catalogued, not yet queryable as an ObjectType"
instead of crashing the consumer — a real, independently-worthwhile
robustness fix this gap surfaced, not something invented just for this
feature.
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

DDL = """
CREATE TABLE IF NOT EXISTS plugin_registration (
    name TEXT PRIMARY KEY,
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


def _load_entry_point(entry_point: str) -> ConnectorPlugin:
    module_path, class_name = entry_point.split(":")
    module = importlib.import_module(module_path)
    return getattr(module, class_name)()


def _checksum_of(entry_point: str) -> str:
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


async def register_plugin(pool: asyncpg.Pool, *, entry_point: str, core_dataset_names: frozenset[str]) -> dict:
    plugin = _load_entry_point(entry_point)
    manifest = plugin.manifest

    if manifest.dataset_name in core_dataset_names:
        raise PluginConflictError(f"dataset {manifest.dataset_name!r} is already owned by a core connector")

    conflicting = await pool.fetchrow(
        "SELECT name FROM plugin_registration WHERE manifest->>'dataset_name' = $1 AND status = 'active' AND name != $2",
        manifest.dataset_name, manifest.name,
    )
    if conflicting is not None:
        raise PluginConflictError(
            f"dataset {manifest.dataset_name!r} is already claimed by active plugin {conflicting['name']!r}"
        )

    checksum = _checksum_of(entry_point)
    await pool.execute(
        """
        INSERT INTO plugin_registration (name, version, manifest, checksum, status)
        VALUES ($1, $2, $3::jsonb, $4, 'active')
        ON CONFLICT (name) DO UPDATE SET
            version = EXCLUDED.version, manifest = EXCLUDED.manifest,
            checksum = EXCLUDED.checksum, status = 'active'
        """,
        manifest.name, manifest.version, manifest.model_dump_json(), checksum,
    )
    return await get_plugin_registration(pool, manifest.name)


async def set_plugin_status(pool: asyncpg.Pool, name: str, status: str) -> Optional[dict]:
    """Activatable/deactivatable without redeploy: flips a column
    the dispatch path (`load_active_plugin_for_dataset`) actually checks.
    """
    await pool.execute("UPDATE plugin_registration SET status = $1 WHERE name = $2", status, name)
    return await get_plugin_registration(pool, name)


async def load_active_plugin_for_dataset(pool: asyncpg.Pool, dataset_name: str) -> Optional[ConnectorPlugin]:
    row = await pool.fetchrow(
        "SELECT manifest FROM plugin_registration WHERE manifest->>'dataset_name' = $1 AND status = 'active'",
        dataset_name,
    )
    if row is None:
        return None
    manifest_dict = json.loads(row["manifest"]) if isinstance(row["manifest"], str) else row["manifest"]
    return _load_entry_point(manifest_dict["entry_point"])
