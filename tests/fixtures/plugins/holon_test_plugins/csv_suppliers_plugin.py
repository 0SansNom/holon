"""Demo CSV suppliers landing connector plugin (register via POST /plugins)."""

from __future__ import annotations

import asyncio
import csv
import io
import os

import pyarrow.fs as pafs

from holon_common.plugin import PluginManifest

_BUCKET_PATH = "holon-warehouse/landing/suppliers.csv"


def _read_suppliers_csv(*, s3_endpoint: str, access_key: str, secret_key: str, region: str) -> list[dict]:
    endpoint_override = s3_endpoint.split("://", 1)[-1]
    fs = pafs.S3FileSystem(
        access_key=access_key,
        secret_key=secret_key,
        endpoint_override=endpoint_override,
        scheme="http",
        region=region,
    )
    with fs.open_input_stream(_BUCKET_PATH) as f:
        raw = f.readall().decode("utf-8")

    reader = csv.DictReader(io.StringIO(raw))
    return [
        {"id": int(row["id"]), "name": row["name"], "country": row["country"], "category": row["category"]}
        for row in reader
    ]


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
        entry_point="holon_test_plugins.csv_suppliers_plugin:CsvSuppliersPlugin",
    )

    async def fetch(self) -> list[dict]:
        return await asyncio.to_thread(
            _read_suppliers_csv,
            s3_endpoint=os.environ["HOLON_S3_ENDPOINT"],
            access_key=os.environ["AWS_ACCESS_KEY_ID"],
            secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            region=os.environ["AWS_REGION"],
        )
