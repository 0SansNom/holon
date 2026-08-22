"""An example **agent tool** plugin. Deliberately has zero ontology involvement — no
ObjectType, no Knowledge Action, no `object_type_urn` anywhere — proving
this is a synthetic capability the Agent Runtime's Tool
Registry can expose to the model.
"""

from __future__ import annotations

import os

import httpx

from holon_common.plugin import PluginManifest


class WeatherLookupPlugin:
    manifest = PluginManifest(
        name="weather-lookup-tool",
        version="1.0.0",
        plugin_type="agent_tool",
        tool_name="weather_lookup",
        tool_description=(
            "Look up the current weather for a country by its ISO 3166-1 alpha-2 "
            "country code (e.g. FR, DE, US). A synthetic example tool, not backed "
            "by any Holon ObjectType or Action."
        ),
        input_schema={
            "type": "object",
            "properties": {"country_code": {"type": "string", "description": "ISO 3166-1 alpha-2 country code"}},
            "required": ["country_code"],
        },
        risk_level="low",
        entry_point="app.plugins.weather_lookup_plugin:WeatherLookupPlugin",
    )

    async def invoke(self, tool_input: dict) -> dict:
        country_code = tool_input.get("country_code", "").upper()
        feed_url = os.environ["HOLON_WEATHER_FEED_URL"]
        async with httpx.AsyncClient() as client:
            response = await client.get(feed_url, timeout=10)
            response.raise_for_status()
        data = response.json()
        return {"country_code": country_code, "weather": data.get(country_code, "unknown")}
