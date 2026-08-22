"""Demo ERP customers connector plugin (register via POST /plugins)."""

from __future__ import annotations

import os

from holon_common.plugin import PluginManifest


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
        entry_point="app.plugins.postgres_customers_plugin:PostgresCustomersPlugin",
    )

    async def fetch(self) -> list[dict]:
        from app import connector

        return await connector.read_customers(os.environ["HOLON_SOURCE_DB_URL"])
