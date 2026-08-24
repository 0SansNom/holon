"""Exchange rate feed connector plugin."""

from __future__ import annotations

import os

import httpx

from holon_common.plugin import PluginManifest


class ExchangeRatePlugin:
    manifest = PluginManifest(
        name="exchange-rate-feed",
        version="1.0.0",
        plugin_type="connector",
        dataset_name="exchange_rates",
        capabilities={"read_only": True},
        permissions_required=["read:external-finance-feed"],
        events_consumed=[],
        events_published=["connectivity.sync.completed"],
        entry_point="holon_test_plugins.exchange_rate_plugin:ExchangeRatePlugin",
    )

    async def fetch(self) -> list[dict]:
        feed_url = os.environ["HOLON_EXCHANGE_RATE_FEED_URL"]
        async with httpx.AsyncClient() as client:
            response = await client.get(feed_url, timeout=10)
            response.raise_for_status()
        return response.json()
