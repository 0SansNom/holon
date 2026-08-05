"""An example third-party-style Connector plugin, proving
connectors are executed without modifying core dispatch logic: this file is the *entire* amount of
code Connectivity's own dispatch path (`main.py`'s `run_sync`) needed to
gain a sixth data source — nothing in `main.py` or `plugin_registry.py`
was written with "exchange rates" in mind. Deliberately lives in its own
`plugins/` package, organizationally separate from the five hand-written
core connectors (`connector.py`, `mongo_connector.py`, etc.) even though
it ships in the same container image for this build's scope — a real
third-party deployment would load this from an independently-versioned
package instead, out of scope here.

Fetches from the same tiny static file server this build already uses
for the REST connector (`reviews-api`) — a second JSON file, no new
container needed.
"""

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
        entry_point="app.plugins.exchange_rate_plugin:ExchangeRatePlugin",
    )

    async def fetch(self) -> list[dict]:
        feed_url = os.environ["HOLON_EXCHANGE_RATE_FEED_URL"]
        async with httpx.AsyncClient() as client:
            response = await client.get(feed_url, timeout=10)
            response.raise_for_status()
        return response.json()
