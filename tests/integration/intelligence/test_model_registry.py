"""Tests for Model Registry."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

import pytest
from conftest import IDENTITY, INTELLIGENCE, KNOWLEDGE, _request, ontology_url, holon_url


_MODEL_ARTIFACT_B64 = (
    "gASVQwIAAAAAAACMFXNrbGVhcm4udHJlZS5fY2xhc3Nlc5SMFkRlY2lzaW9uVHJlZUNsYXNzaWZpZXKU"
    "k5QpgZR9lCiMCWNyaXRlcmlvbpSMBGdpbmmUjAhzcGxpdHRlcpSMBGJlc3SUjAltYXhfZGVwdGiUSwKM"
    "EW1pbl9zYW1wbGVzX3NwbGl0lEsCjBBtaW5fc2FtcGxlc19sZWFmlEsBjBhtaW5fd2VpZ2h0X2ZyYWN0"
    "aW9uX2xlYWaURwAAAAAAAAAAjAxtYXhfZmVhdHVyZXOUTowObWF4X2xlYWZfbm9kZXOUTowMcmFuZG9t"
    "X3N0YXRllEsAjBVtaW5faW1wdXJpdHlfZGVjcmVhc2WURwAAAAAAAAAAjAxjbGFzc193ZWlnaHSUTowJ"
    "Y2NwX2FscGhhlEcAAAAAAAAAAIwNbW9ub3RvbmljX2NzdJROjA5uX2ZlYXR1cmVzX2luX5RLAYwKbl9v"
    "dXRwdXRzX5RLAYwIY2xhc3Nlc1+UjBNqb2JsaWIubnVtcHlfcGlja2xllIwRTnVtcHlBcnJheVdyYXBw"
    "ZXKUk5QpgZR9lCiMCHN1YmNsYXNzlIwFbnVtcHmUjAduZGFycmF5lJOUjAVzaGFwZZRLA4WUjAVvcmRl"
    "cpSMAUOUjAVkdHlwZZRoHYwFZHR5cGWUk5SMAmk4lImIh5RSlChLA4wBPJROTk5K/////0r/////SwB0"
    "lGKMCmFsbG93X21tYXCUiIwbbnVtcHlfYXJyYXlfYWxpZ25tZW50X2J5dGVzlEsQdWIB/wAAAAAAAAAA"
    "AQAAAAAAAAACAAAAAAAAAJWfAAAAAAAAAIwKbl9jbGFzc2VzX5SMFm51bXB5Ll9jb3JlLm11bHRpYXJy"
    "YXmUjAZzY2FsYXKUk5RoKUMIAwAAAAAAAACUhpRSlIwNbWF4X2ZlYXR1cmVzX5RLAYwFdHJlZV+UjBJz"
    "a2xlYXJuLnRyZWUuX3RyZWWUjARUcmVllJOUSwFoGSmBlH2UKGgcaB9oIEsBhZRoImgjaCRoKWgsiGgt"
    "SxB1Yg////////////////////8DAAAAAAAAAJWNAQAAAAAAAEsBh5RSlH2UKGgJSwKMCm5vZGVfY291"
    "bnSUSwWMBW5vZGVzlGgZKYGUfZQoaBxoH2ggSwWFlGgiaCNoJGgmjANWNjSUiYiHlFKUKEsDjAF8lE4o"
    "jApsZWZ0X2NoaWxklIwLcmlnaHRfY2hpbGSUjAdmZWF0dXJllIwJdGhyZXNob2xklIwIaW1wdXJpdHmU"
    "jA5uX25vZGVfc2FtcGxlc5SMF3dlaWdodGVkX25fbm9kZV9zYW1wbGVzlIwSbWlzc2luZ19nb190b19s"
    "ZWZ0lHSUfZQoaEloJowCaTiUiYiHlFKUKEsDaCpOTk5K/////0r/////SwB0lGJLAIaUaEpoVUsIhpRo"
    "S2hVSxCGlGhMaCaMAmY4lImIh5RSlChLA2gqTk5OSv////9K/////0sAdJRiSxiGlGhNaFxLIIaUaE5o"
    "VUsohpRoT2hcSzCGlGhQaCaMAnUxlImIh5RSlChLA2hITk5OSv////9K/////0sAdJRiSziGlHVLQEsB"
    "SxB0lGJoLIhoLUsQdWIB/wEAAAAAAAAAAgAAAAAAAAAAAAAAAAAAAAAAAAAAiMNAVlVVVVVV5T8GAAAA"
    "AAAAAAAAAAAAABhAAAAAAAAAAAD//////////////////////v////////8AAAAAAAAAwAAAAAAAAAAA"
    "AgAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAwAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAAAAAAD59UAAAAAA"
    "AADgPwQAAAAAAAAAAAAAAAAAEEAAAAAAAAAAAP/////////////////////+/////////wAAAAAAAADA"
    "AAAAAAAAAAACAAAAAAAAAAAAAAAAAABAAAAAAAAAAAD//////////////////////v////////8AAAAA"
    "AAAAwAAAAAAAAAAAAgAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAlTAAAAAAAAAAjAZ2YWx1ZXOUaBkpgZR9"
    "lChoHGgfaCBLBUsBSwOHlGgiaCNoJGhcaCyIaC1LEHViBv///////1VVVVVVVdU/VVVVVVVV1T9VVVVV"
    "VVXVPwAAAAAAAPA/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA4D8AAAAAAADgPwAAAAAAAAAA"
    "AAAAAAAA8D8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAADwP5UgAAAAAAAAAHVijBBfc2tsZWFy"
    "bl92ZXJzaW9ulIwFMS41LjKUdWIu"
)


def _token_for(principal_urn: str) -> str:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        local_name = principal_urn.rsplit(":", 1)[-1]
        status, body = _request(
            "POST",
            f"{IDENTITY}/token",
            body={"principal_urn": principal_urn, "client_secret": f"{local_name}-dev-secret"},
        )
        if status == 200:
            return body["access_token"]
        time.sleep(1.5)
    pytest.fail(f"could not mint a token for {principal_urn}")


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


_INPUT_SCHEMA = {"type": "object", "properties": {"lifetimeValue": {"type": "number"}}, "required": ["lifetimeValue"]}


def _register_model(token: str, name: str) -> dict:
    status, body = _request(
        "POST", f"{INTELLIGENCE}/models/{name}", token=token,
        body={"version": "1.0.0", "framework": "sklearn", "artifact_base64": _MODEL_ARTIFACT_B64, "input_schema": _INPUT_SCHEMA},
    )
    assert status == 200, body
    return body


def test_registering_an_invalid_artifact_is_rejected(jdoe_token: str) -> None:
    status, body = _request(
        "POST", f"{INTELLIGENCE}/models/{_unique_name('bad-model')}", token=jdoe_token,
        body={"version": "1.0.0", "framework": "sklearn", "artifact_base64": "bm90IGEgcmVhbCBtb2RlbA==", "input_schema": _INPUT_SCHEMA},
    )
    assert status == 400, body
    assert "does not deserialize" in body["detail"], body


def test_registering_with_an_unknown_framework_is_rejected(jdoe_token: str) -> None:
    status, body = _request(
        "POST", f"{INTELLIGENCE}/models/{_unique_name('bad-framework')}", token=jdoe_token,
        body={"version": "1.0.0", "framework": "tensorflow", "artifact_base64": _MODEL_ARTIFACT_B64, "input_schema": _INPUT_SCHEMA},
    )
    assert status == 400, body
    assert "unknown framework" in body["detail"], body


def test_register_and_predict_real_values(jdoe_token: str) -> None:
    name = _unique_name("value-classifier")
    registration = _register_model(jdoe_token, name)
    assert registration["status"] == "active", registration

    status, fetched = _request("GET", f"{INTELLIGENCE}/models/{name}", token=jdoe_token)
    assert status == 200 and fetched["name"] == name, fetched

    status, listed = _request("GET", f"{INTELLIGENCE}/models", token=jdoe_token)
    assert status == 200 and any(m["name"] == name for m in listed), listed

    # Real, deterministic predictions from the trained decision tree, not
    # a stub
    status, low = _request("POST", f"{INTELLIGENCE}/models/{name}/predict", token=jdoe_token, body={"features": {"lifetimeValue": 2000}})
    assert status == 200, low
    status, high = _request("POST", f"{INTELLIGENCE}/models/{name}/predict", token=jdoe_token, body={"features": {"lifetimeValue": 184500}})
    assert status == 200, high
    assert low["prediction"] != high["prediction"], (low, high)
    assert isinstance(low["prediction"], int), low


def test_predict_with_missing_feature_is_rejected(jdoe_token: str) -> None:
    name = _unique_name("value-classifier")
    _register_model(jdoe_token, name)
    status, body = _request("POST", f"{INTELLIGENCE}/models/{name}/predict", token=jdoe_token, body={"features": {}})
    assert status == 400, body
    assert "lifetimeValue" in body["detail"], body


def test_predict_against_an_unknown_model_is_404(jdoe_token: str) -> None:
    status, body = _request(
        "POST", f"{INTELLIGENCE}/models/{_unique_name('never-registered')}/predict", token=jdoe_token,
        body={"features": {"lifetimeValue": 1000}},
    )
    assert status == 404, body


def test_disable_gates_predict_and_enable_restores_it(jdoe_token: str, msmith_token: str) -> None:
    name = _unique_name("value-classifier")
    _register_model(jdoe_token, name)

    status, disabled = _request("POST", f"{INTELLIGENCE}/models/{name}/disable", token=msmith_token)
    assert status == 200 and disabled["status"] == "disabled", disabled

    status, body = _request("POST", f"{INTELLIGENCE}/models/{name}/predict", token=jdoe_token, body={"features": {"lifetimeValue": 1000}})
    assert status == 403, body
    assert "not active" in body["detail"], body

    status, enabled = _request("POST", f"{INTELLIGENCE}/models/{name}/enable", token=msmith_token)
    assert status == 200 and enabled["status"] == "active", enabled
    status, body = _request("POST", f"{INTELLIGENCE}/models/{name}/predict", token=jdoe_token, body={"features": {"lifetimeValue": 1000}})
    assert status == 200, body


def test_model_backed_function_is_a_real_derived_property_and_respects_masking(
    jdoe_token: str, msmith_token: str, kenji_token: str
) -> None:
    """`customer_value_model_function.py` calls the hardcoded."""
    _register_model(msmith_token, "customer-value-classifier")

    status, registration = _request(
        "POST", holon_url("/function-plugins"), token=msmith_token,
        body={"entry_point": "app.plugins.customer_value_model_function:CustomerValueModelFunction"},
    )
    assert status == 200, registration
    assert registration["manifest"]["function_name"] == "predict_customer_value_tier", registration

    status, draft = _request(
        "POST", ontology_url("/objectTypes/Customer/versions"), token=msmith_token,
        body={"derived_properties": {"mlValueTier": "predict_customer_value_tier"}},
    )
    assert status == 201, draft
    status, published = _request(
        "POST", ontology_url(f"/objectTypes/Customer/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 200, published
    assert published["derived_properties"] == {"mlValueTier": "predict_customer_value_tier"}, published

    # Customer 1 (Acme Robotics) seeds at lifetimeValue 184500
    # real, non-stub prediction from the trained tree, computed fresh.
    status, customer = _request("GET", ontology_url("/objects/Customer/1"), token=jdoe_token)
    assert status == 200, customer
    assert customer["mlValueTier"] is not None, customer
    assert isinstance(customer["mlValueTier"], int), customer

    # kenji (ABAC-denied): lifetime_value is masked to None, so the
    # derived property
    # skipped entirely, the same R8.7 interaction  already proved
    # for the rule-based Function.
    status, masked_customer = _request("GET", ontology_url("/objects/Customer/1"), token=kenji_token)
    assert status == 200, masked_customer
    assert "lifetime_value" in masked_customer.get("_maskedFields", []), masked_customer
    assert "mlValueTier" not in masked_customer, masked_customer


def test_a_failing_model_backed_derived_property_degrades_gracefully_not_a_500(
    jdoe_token: str, msmith_token: str
) -> None:
    """Verifies that an exception in a Function-backed derived property (e.g., when the."""
    _register_model(msmith_token, "customer-value-classifier")
    status, draft = _request(
        "POST", ontology_url("/objectTypes/Customer/versions"), token=msmith_token,
        body={"derived_properties": {"mlValueTier": "predict_customer_value_tier"}},
    )
    assert status == 201, draft
    status, published = _request(
        "POST", ontology_url(f"/objectTypes/Customer/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 200, published

    status, disabled = _request("POST", f"{INTELLIGENCE}/models/customer-value-classifier/disable", token=msmith_token)
    assert status == 200, disabled
    try:
        status, customer = _request("GET", ontology_url("/objects/Customer/1"), token=jdoe_token)
        assert status == 200, customer
        assert "mlValueTier" not in customer, customer
    finally:
        status, enabled = _request("POST", f"{INTELLIGENCE}/models/customer-value-classifier/enable", token=msmith_token)
        assert status == 200, enabled

    status, customer = _request("GET", ontology_url("/objects/Customer/1"), token=jdoe_token)
    assert status == 200, customer
    assert customer["mlValueTier"] is not None, customer
