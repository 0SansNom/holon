"""Example **Function** plugin for a *function-backed Action Type*
(`ontology/action_types.py`'s `edit_function`, `actions._resolve_
function_backed_edits`) — the plugin's return value BECOMES the applied
edits, unlike `lifetime_tier_function.py`'s use as a read-time derived
property or an Action's fire-and-forget `function_side_effect`. Pure,
no external I/O, same shape as `lifetime_tier_function.py` on purpose —
demonstrates the mechanism without conflating it with the model-serving
example (`customer_value_model_function.py`).
"""

from __future__ import annotations

from holon_common.plugin import PluginManifest

_THRESHOLDS = (
    (100_000, "enterprise"),
    (20_000, "mid-market"),
)


class SegmentFromValueFunction:
    manifest = PluginManifest(
        name="segment-from-value-function",
        version="1.0.0",
        plugin_type="function",
        function_name="segment_from_value",
        input_schema={
            "type": "object",
            "properties": {"lifetimeValue": {"type": "number"}},
            "required": ["lifetimeValue"],
        },
        entry_point="app.plugins.segment_from_value_function:SegmentFromValueFunction",
    )

    async def call(self, **kwargs) -> dict:
        value = float(kwargs.get("lifetimeValue") or 0)
        segment = "smb"
        for threshold, name in _THRESHOLDS:
            if value >= threshold:
                segment = name
                break
        return {"segment": segment}
