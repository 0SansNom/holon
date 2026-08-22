"""An example **export format** plugin — adds CSV alongside the built-in JSON,
using nothing but the stdlib `csv` module (zero new dependencies).
"""

from __future__ import annotations

import csv
import io

from holon_common.plugin import PluginManifest


class CsvExportPlugin:
    manifest = PluginManifest(
        name="csv-export",
        version="1.0.0",
        plugin_type="export_format",
        format_name="csv",
        content_type="text/csv",
        entry_point="app.plugins.csv_export_plugin:CsvExportPlugin",
    )

    def serialize(self, rows: list[dict]) -> bytes:
        if not rows:
            return b""
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        return buffer.getvalue().encode("utf-8")
