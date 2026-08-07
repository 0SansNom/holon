"""Functions — the sixth plugin type. Proves both real call sites:
(1) a read-time derived property on Customer (`tier`, computed from
`lifetimeValue` via the registered `lifetime_tier` Function plugin),
correctly skipped when its required input was masked rather than computed
from a misleading `None`; (2) `Customer.putOnCreditHold`'s
`function_side_effect` — best-effort, must never break the primary Action
even though it's wired in. No real LLM calls.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import IDENTITY, KNOWLEDGE, _request


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


@pytest.fixture(scope="session")
def registered(msmith_token: str) -> dict:
    """Registration is idempotent (`ON CONFLICT (name) DO UPDATE`), so a
    repeated run against leftover state just re-registers the same plugin —
    same idiom already used by the execution-adapter/export-format tests.
    """
    status, registration = _request(
        "POST", f"{KNOWLEDGE}/function-plugins", token=msmith_token,
        body={"entry_point": "app.plugins.lifetime_tier_function:LifetimeTierFunction"},
    )
    assert status == 200, registration
    assert registration["manifest"]["function_name"] == "lifetime_tier", registration
    return registration


def test_unregistered_function_is_rejected_at_publish_time(msmith_token: str) -> None:
    status, draft = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/versions", token=msmith_token,
        body={"derived_properties": {"tier": "this_function_does_not_exist"}},
    )
    assert status == 201, draft
    status, publish_result = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/versions/{draft['version']}/publish", token=msmith_token
    )
    assert status == 400, publish_result
    assert "not a registered, active Function plugin" in publish_result["detail"], publish_result


def test_derived_property_is_computed_on_read_and_masked_when_its_input_is(
    registered: dict, msmith_token: str, jdoe_token: str, kenji_token: str
) -> None:
    status, draft = _request(
        "POST", f"{KNOWLEDGE}/ontology/Customer/versions", token=msmith_token,
        body={"derived_properties": {"tier": "lifetime_tier"}},
    )
    assert status == 201, draft
    status, published = _request(
        "POST", f"{KNOWLEDGE}/ontology/Customer/versions/{draft['version']}/publish", token=msmith_token
    )
    assert status == 200, published
    assert published["derived_properties"] == {"tier": "lifetime_tier"}, published

    # jdoe (FR, ABAC-allowed): sees the real lifetimeValue, so `tier` is
    # genuinely computed from it. Customer 1 (Acme Robotics) seeds at
    # 184500.00 — platinum (>= 150_000).
    status, customer = _request("GET", f"{KNOWLEDGE}/objects/Customer/1", token=jdoe_token)
    assert status == 200, customer
    assert customer["tier"] == "platinum", customer

    # kenji (JP, ABAC-denied on confidential fields): lifetime_value is
    # masked to None, so `tier` — whose required input is missing — must
    # be skipped entirely, never silently computed as "bronze" from the
    # masked value.
    status, masked_customer = _request("GET", f"{KNOWLEDGE}/objects/Customer/1", token=kenji_token)
    assert status == 200, masked_customer
    assert "lifetime_value" in masked_customer.get("_maskedFields", []), masked_customer
    assert "tier" not in masked_customer, masked_customer


def test_put_on_credit_hold_still_succeeds_with_its_function_side_effect_wired(jdoe_token: str) -> None:
    """`Customer.putOnCreditHold` declares `function_side_effect:
    "lifetime_tier"` — best-effort, must never break the primary Action.
    """
    status, result = _request(
        "POST", f"{KNOWLEDGE}/objects/Customer/2/actions/putOnCreditHold", token=jdoe_token,
        body={"reason": "test_functions.py side-effect regression check"},
    )
    assert status == 200, result
    assert result["status"] == "applied", result
