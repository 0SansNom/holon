"""Demo CSV suppliers landing connector plugin (register via POST /plugins)."""

from __future__ import annotations

import asyncio
import os

from holon_common.plugin import PluginManifest


class CsvSuppliersPlugin:
    manifest = PluginManifest(
        name="csv-suppliers",
        version="1.0.0",
        plugin_type="connector",
        dataset_name="suppliers",
        connector_local_name="csv-landing-suppliers",
        capabilities={"read_only": True},
        permissions_required=[],
        events_consumed=[],
        events_published=["connectivity.sync.completed"],
        entry_point="app.plugins.csv_suppliers_plugin:CsvSuppliersPlugin",
    )

    async def fetch(self) -> list[dict]:
        from app import file_connector

        return await asyncio.to_thread(
            file_connector.read_suppliers_csv,
            s3_endpoint=os.environ["HOLON_S3_ENDPOINT"],
            access_key=os.environ["AWS_ACCESS_KEY_ID"],
            secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            region=os.environ["AWS_REGION"],
        )
