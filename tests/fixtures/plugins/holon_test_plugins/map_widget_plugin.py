"""An example **UI component** plugin — a `map` component (table, detail,
relation graph, time series, map, KPI).
"""

from __future__ import annotations

from holon_common.plugin import PluginManifest


class MapWidgetPlugin:
    manifest = PluginManifest(
        name="map-widget",
        version="1.0.0",
        plugin_type="ui_component",
        component_name="map",
        binding_contract={"requiredProperties": ["country"]},
        iframe_url="http://reviews-api:8000/map-widget.html",
        entry_point="holon_test_plugins.map_widget_plugin:MapWidgetPlugin",
    )
