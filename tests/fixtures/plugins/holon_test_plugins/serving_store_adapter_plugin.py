"""An example **execution adapter** plugin. Registers against `Supplier` — a
standalone ObjectType with no relation traversal depending on it,
a low-risk target to prove the adapter interface is genuinely swappable
without touching anything else's execution path.

A genuinely different query engine from the built-in DuckDB-over-Iceberg
adapter: plain Postgres JSONB queries against Knowledge's own
already-materialized `object_instance` table (`serving_store.py`) — the
exact same data DuckDB's adapter would otherwise scan from Iceberg,
reached through a completely different engine, proving the *interface*
swap (`execution.get_or_execute` doesn't change at all) rather than
building a genuinely separate storage backend just to make the point.
"""

from __future__ import annotations

import json

import asyncpg

from holon_common.plugin import PluginManifest


class ServingStoreAdapterPlugin:
    manifest = PluginManifest(
        name="serving-store-adapter",
        version="1.0.0",
        plugin_type="execution_adapter",
        adapter_object_type="Supplier",
        entry_point="app.plugins.serving_store_adapter_plugin:ServingStoreAdapterPlugin",
    )

    async def execute(
        self,
        pool: asyncpg.Pool,
        *,
        object_type: str,
        tenant_id: str,
        filter_property: str,
        filter_value: str,
        operation: str,
    ):
        if operation == "count":
            count = await pool.fetchval(
                "SELECT COUNT(*) FROM object_instance WHERE object_type = $1 AND tenant_id = $2 AND data->>$3 = $4",
                object_type, tenant_id, filter_property, filter_value,
            )
            return {"count": count}

        rows = await pool.fetch(
            "SELECT data FROM object_instance WHERE object_type = $1 AND tenant_id = $2 AND data->>$3 = $4",
            object_type, tenant_id, filter_property, filter_value,
        )
        return [json.loads(row["data"]) if isinstance(row["data"], str) else row["data"] for row in rows]
