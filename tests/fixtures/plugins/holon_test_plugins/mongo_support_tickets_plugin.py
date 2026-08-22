"""Demo support-desk tickets connector plugin (register via POST /plugins)."""

from __future__ import annotations

import asyncio
import os

from holon_common.plugin import PluginManifest


class MongoSupportTicketsPlugin:
    manifest = PluginManifest(
        name="mongo-support-tickets",
        version="1.0.0",
        plugin_type="connector",
        dataset_name="support_tickets",
        connector_local_name="mongodb-support-desk",
        capabilities={"read_only": True},
        permissions_required=[],
        events_consumed=[],
        events_published=["connectivity.sync.completed"],
        entry_point="holon_test_plugins.mongo_support_tickets_plugin:MongoSupportTicketsPlugin",
    )

    async def fetch(self) -> list[dict]:
        from app import mongo_connector

        return await asyncio.to_thread(
            mongo_connector.read_support_tickets, os.environ["HOLON_MONGO_URL"]
        )
