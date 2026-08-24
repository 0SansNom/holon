"""Demo support-desk tickets connector plugin (register via POST /plugins)."""

from __future__ import annotations

import asyncio
import os

from pymongo import MongoClient

from holon_common.plugin import PluginManifest

_DATABASE = "support_desk"
_COLLECTION = "support_tickets"


def _read_support_tickets(mongo_url: str) -> list[dict]:
    client = MongoClient(mongo_url)
    try:
        cursor = client[_DATABASE][_COLLECTION].find({}, {"_id": 0}).sort("id", 1)
        return list(cursor)
    finally:
        client.close()


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
        return await asyncio.to_thread(_read_support_tickets, os.environ["HOLON_MONGO_URL"])
