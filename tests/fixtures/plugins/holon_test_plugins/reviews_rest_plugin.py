"""Demo product-reviews REST connector plugin (register via POST /plugins)."""

from __future__ import annotations

import os

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
        entry_point="app.plugins.reviews_rest_plugin:ReviewsRestPlugin",
    )

    async def fetch(self) -> list[dict]:
        from app import rest_connector

        return await rest_connector.read_reviews(os.environ["HOLON_REVIEWS_API_URL"])
