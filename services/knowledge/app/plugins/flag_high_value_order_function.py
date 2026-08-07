"""A second example **Function** plugin — this one exercises the third
call site (Phase C, Pipeline TransformSteps: `POST /functions/{name}/invoke`),
alongside `lifetime_tier_function.py`'s derived-property/Action-side-effect
call sites. The contract here is different on purpose: a pipeline step
treats the Function as a row -> row map over an entire Iceberg table, so
the plugin's return value *is* the full output row, not a partial dict
merged into an existing ObjectType instance. Flags an Order row as
high-value (amount >= $1000) — real, checkable logic, not a stub.
"""

from __future__ import annotations

from holon_common.plugin import PluginManifest

_HIGH_VALUE_THRESHOLD = 1000


class FlagHighValueOrderFunction:
    manifest = PluginManifest(
        name="flag-high-value-order-function",
        version="1.0.0",
        plugin_type="function",
        function_name="flag_high_value_order",
        input_schema={
            "type": "object",
            "properties": {"amount": {"type": "number"}},
            "required": ["amount"],
        },
        entry_point="app.plugins.flag_high_value_order_function:FlagHighValueOrderFunction",
    )

    async def call(self, **kwargs) -> dict:
        amount = float(kwargs.get("amount") or 0)
        return {**kwargs, "high_value": amount >= _HIGH_VALUE_THRESHOLD}
