"""Tests for Authorization."""

from __future__ import annotations


from conftest import TENANT_ID, _request, ontology_url, holon_url


def test_workspace_viewer_in_allowed_country_is_granted(jdoe_token: str) -> None:
    status, customers = _request("GET", ontology_url("/objects/Customer"), token=jdoe_token)
    assert status == 200, customers
    assert len(customers) > 0


def test_tenant_member_without_workspace_access_is_denied_by_rebac(alice_token: str) -> None:
    status, body = _request("GET", ontology_url("/objects/Customer"), token=alice_token)
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body


def test_workspace_viewer_in_disallowed_country_gets_confidential_fields_masked_not_denied(kenji_token: str) -> None:
    """A disallowed-country principal used to be denied the whole."""
    status, customers = _request("GET", ontology_url("/objects/Customer"), token=kenji_token)
    assert status == 200, customers
    assert len(customers) > 0
    for customer in customers:
        assert customer["email"] is None, customer
        assert customer["lifetime_value"] is None, customer
        assert set(customer["_maskedFields"]) == {"email", "lifetime_value"}, customer
        assert customer["name"], customer  # non-confidential fields untouched
        assert customer["country"], customer


def test_lineage_endpoint_is_governed_by_the_same_pdp(alice_token: str, jdoe_token: str) -> None:
    status, body = _request("GET", holon_url(f"/lineage/hl:{TENANT_ID}:main:object-type:Customer"), token=alice_token)
    assert status == 403, body

    status, _ = _request("GET", holon_url(f"/lineage/hl:{TENANT_ID}:main:object-type:Customer"), token=jdoe_token)
    assert status == 200
