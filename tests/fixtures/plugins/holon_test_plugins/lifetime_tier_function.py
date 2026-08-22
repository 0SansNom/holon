"""An example **Function** plugin — real, not a toy: computes a
customer's loyalty tier from `lifetimeValue`. Used from two call sites
to prove the mechanism genuinely generalizes, not hardwired to one:
(1) as a read-time derived property (declared in an ObjectType version's
`derived_properties`), (2) as an Action side effect (declared via
`function_side_effect` on an ActionType, invoked after the Action
applies).
"""

from __future__ import annotations

from holon_common.plugin import PluginManifest

_THRESHOLDS = (
    (150_000, "platinum"),
    (50_000, "gold"),
    (10_000, "silver"),
)


class LifetimeTierFunction:
    manifest = PluginManifest(
        name="lifetime-tier-function",
        version="1.0.0",
        plugin_type="function",
        function_name="lifetime_tier",
        input_schema={
            "type": "object",
            "properties": {"lifetimeValue": {"type": "number"}},
            "required": ["lifetimeValue"],
        },
        entry_point="app.plugins.lifetime_tier_function:LifetimeTierFunction",
    )

    async def call(self, **kwargs) -> dict:
        value = float(kwargs.get("lifetimeValue") or 0)
        tier = "bronze"
        for threshold, name in _THRESHOLDS:
            if value >= threshold:
                tier = name
                break
        return {"tier": tier}
