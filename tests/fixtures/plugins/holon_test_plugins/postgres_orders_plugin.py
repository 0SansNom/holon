"""Demo ERP orders connector plugin (register via POST /plugins)."""

from __future__ import annotations

import os

from holon_common.plugin import PluginManifest


class PostgresOrdersPlugin:
    manifest = PluginManifest(
        name="postgres-orders",
        version="1.0.0",
        plugin_type="connector",
        dataset_name="orders",
        connector_local_name="postgres-source-erp",
        capabilities={"read_only": True},
        permissions_required=[],
        events_consumed=[],
        events_published=["connectivity.sync.completed"],
        entry_point="holon_test_plugins.postgres_orders_plugin:PostgresOrdersPlugin",
    )

    async def fetch(self) -> list[dict]:
        from app import connector

        return await connector.read_orders(os.environ["HOLON_SOURCE_DB_URL"])
