"""A third example **Function** plugin — the one that closes platform's
loop ("callable from a  Function as a derived-property
computation"): where `lifetime_tier_function.py` computes a tier with
hand-written thresholds, this one asks an actually-registered scikit-
learn model (Intelligence's `POST /models/{name}/predict`, Phase F) for
the same kind of answer — a real cross-service call, not a second copy
of the same logic. Mints its own short-lived service-account token
(same pattern as every other internal service-to-service call in this
build: Knowledge's own `_identity_validation_token`, Connectivity's
`_function_invocation_token`, Experience's `_agent_app_session_token`)
since a Function plugin, like every plugin type, has no principal of its
own to act as — `load_entry_point` just does `ClassName()`, no
constructor injection.

Expects a model already registered under `MODEL_NAME` — this plugin
does not train or register anything itself, matching platform's own
"no training infrastructure" scope note; if the model isn't registered,
`call()` surfaces whatever error Intelligence's own `/predict` endpoint
returns, the same as any other unmet dependency in this build.
"""

from __future__ import annotations

import os

import httpx

from holon_common import Principal, active_jwt, build_urn, issue_token
from holon_common.plugin import PluginManifest

MODEL_NAME = "customer-value-classifier"


def _caller_token() -> str:
    tenant_id = os.environ["HOLON_TENANT_ID"]
    secret, kid, secrets_map = active_jwt()
    principal = Principal(
        urn=build_urn(tenant_id, "global", "service-account", "knowledge-model-caller"),
        type="service_account",
        tenant_id=tenant_id,
        display_name="Knowledge Model Caller",
    )
    return issue_token(principal, secret, ttl_seconds=60, kid=kid, secrets=secrets_map)


class CustomerValueModelFunction:
    manifest = PluginManifest(
        name="customer-value-model-function",
        version="1.0.0",
        plugin_type="function",
        function_name="predict_customer_value_tier",
        input_schema={
            "type": "object",
            "properties": {"lifetimeValue": {"type": "number"}},
            "required": ["lifetimeValue"],
        },
        entry_point="app.plugins.customer_value_model_function:CustomerValueModelFunction",
    )

    async def call(self, **kwargs) -> dict:
        intelligence_url = os.environ["HOLON_INTELLIGENCE_URL"]
        lifetime_value = float(kwargs.get("lifetimeValue") or 0)
        token = _caller_token()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{intelligence_url}/models/{MODEL_NAME}/predict",
                headers={"Authorization": f"Bearer {token}"},
                json={"features": {"lifetimeValue": lifetime_value}},
            )
        response.raise_for_status()
        return {"mlValueTier": response.json()["prediction"]}
