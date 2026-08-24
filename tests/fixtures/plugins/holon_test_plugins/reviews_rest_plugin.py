"""Demo product-reviews REST connector plugin (register via POST /plugins)."""

from __future__ import annotations

import os

import httpx

from holon_common.plugin import PluginManifest


class ReviewsRestPlugin:
    manifest = PluginManifest(
        name="reviews-rest",
        version="1.0.0",
        plugin_type="connector",
        dataset_name="reviews",
        connector_local_name="reviews-rest-api",
        capabilities={"read_only": True},
        permissions_required=[],
        events_consumed=[],
        events_published=["connectivity.sync.completed"],
        entry_point="holon_test_plugins.reviews_rest_plugin:ReviewsRestPlugin",
    )

    async def fetch(self) -> list[dict]:
        async with httpx.AsyncClient() as client:
            response = await client.get(os.environ["HOLON_REVIEWS_API_URL"], timeout=10)
            response.raise_for_status()
        return response.json()
