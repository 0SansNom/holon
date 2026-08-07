"""Property formats — value/conditional formatting metadata on an
ObjectType property (Foundry's own "value formatting and conditional
formatting" note), governed the same versioned way `derived_properties`
already is (`test_functions.py`'s pattern): a draft, publish-time
validation, then live on the published ObjectType. Presentation-only —
never rewrites the underlying value, only describes how to render it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import IDENTITY, KNOWLEDGE, TENANT_ID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libs"))

from holon_sdk import HolonClient  # noqa: E402


client = HolonClient(identity_url=IDENTITY)
_request = client.request


@pytest.fixture(scope="session")
def msmith_token() -> str:
    try:
        return client.token_for(f"hl:{TENANT_ID}:global:user:msmith")
    except TimeoutError as exc:
        pytest.fail(str(exc))


def test_format_rule_naming_an_unknown_property_is_rejected_at_publish_time(msmith_token: str) -> None:
    status, draft = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/versions", token=msmith_token,
        body={"property_formats": {"thisPropertyDoesNotExist": {"kind": "currency", "currency": "USD"}}},
    )
    assert status == 201, draft
    status, publish_result = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/versions/{draft['version']}/publish", token=msmith_token
    )
    assert status == 400, publish_result
    assert "doesn't have" in publish_result["detail"], publish_result


def test_badge_rule_with_an_unknown_color_is_rejected_at_publish_time(msmith_token: str) -> None:
    status, draft = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/versions", token=msmith_token,
        body={"property_formats": {"category": {"kind": "badge", "colors": {"raw-materials": "not-a-real-color"}}}},
    )
    assert status == 201, draft
    status, publish_result = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/versions/{draft['version']}/publish", token=msmith_token
    )
    assert status == 400, publish_result
    assert "unknown badge color" in publish_result["detail"], publish_result


def test_currency_and_badge_format_rules_publish_and_appear_on_the_live_object_type(msmith_token: str) -> None:
    status, draft = _request(
        "POST", f"{KNOWLEDGE}/ontology/Customer/versions", token=msmith_token,
        body={
            "property_formats": {
                "lifetimeValue": {"kind": "currency", "currency": "USD"},
                "segment": {"kind": "badge", "colors": {"enterprise": "primary", "mid-market": "success", "smb": "none"}},
            }
        },
    )
    assert status == 201, draft
    assert draft["property_formats"]["lifetimeValue"] == {"kind": "currency", "currency": "USD"}, draft

    status, published = _request(
        "POST", f"{KNOWLEDGE}/ontology/Customer/versions/{draft['version']}/publish", token=msmith_token
    )
    assert status == 200, published
    assert published["property_formats"]["lifetimeValue"] == {"kind": "currency", "currency": "USD"}, published
    assert published["property_formats"]["segment"]["colors"]["enterprise"] == "primary", published

    status, live = _request("GET", f"{KNOWLEDGE}/ontology/Customer", token=msmith_token)
    assert status == 200, live
    assert live["property_formats"] == published["property_formats"], live
