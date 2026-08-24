"""Demo ERP customers connector plugin (register via POST /plugins)."""

from __future__ import annotations

import os

import asyncpg

from holon_common.plugin import PluginManifest

_QUERY = """
    SELECT id, name, email, country, segment, lifetime_value, updated_at
    FROM customers
    ORDER BY id
"""


class PostgresCustomersPlugin:
    manifest = PluginManifest(
        name="postgres-customers",
        version="1.0.0",
        plugin_type="connector",
        dataset_name="customers",
        connector_local_name="postgres-source-erp",
        capabilities={"read_only": True},
        permissions_required=[],
        events_consumed=[],
        events_published=["connectivity.sync.completed"],
        entry_point="holon_test_plugins.postgres_customers_plugin:PostgresCustomersPlugin",
    )

    async def fetch(self) -> list[dict]:
        conn = await asyncpg.connect(os.environ["HOLON_SOURCE_DB_URL"])
        try:
            rows = await conn.fetch(_QUERY)
        finally:
            await conn.close()
        return [dict(row) for row in rows]
