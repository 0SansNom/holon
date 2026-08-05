"""End-to-end verification of the ReBAC + ABAC PDP.

Black-box over HTTP. Requires the stack running (`make up`) with its seed data:
jdoe (workspace viewer, FR) is granted by both engines; alice (tenant member only, US) is
denied by ReBAC alone; kenji (workspace viewer, JP) is granted by ReBAC
but ABAC-restricted — proving evaluation order (ReBAC grants, then ABAC
restricts) and property granularity masking.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

IDENTITY = "http://localhost:8001"
KNOWLEDGE = "http://localhost:8003"

TENANT_ID = "acme"


def _request(method: str, url: str, *, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


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
    pytest.fail(f"could not mint a token for {principal_urn} — is `make up` running with authz seeded?")


@pytest.fixture(scope="session")
def jdoe_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:jdoe")


@pytest.fixture(scope="session")
def alice_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:alice")


@pytest.fixture(scope="session")
def kenji_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:kenji")


def test_workspace_viewer_in_allowed_country_is_granted(jdoe_token: str) -> None:
    status, customers = _request("GET", f"{KNOWLEDGE}/objects/Customer", token=jdoe_token)
    assert status == 200, customers
    assert len(customers) > 0


def test_tenant_member_without_workspace_access_is_denied_by_rebac(alice_token: str) -> None:
    status, body = _request("GET", f"{KNOWLEDGE}/objects/Customer", token=alice_token)
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body


def test_workspace_viewer_in_disallowed_country_gets_confidential_fields_masked_not_denied(kenji_token: str) -> None:
    """A disallowed-country principal used to be denied the whole
    ObjectType (403 abac_denied). Now ReBAC still grants the read; ABAC's
    country restriction is enforced at property granularity instead:
    `email`/`lifetime_value` (Customer's confidential columns) come back
    `None` and named in `_maskedFields`, every other property is intact.
    """
    status, customers = _request("GET", f"{KNOWLEDGE}/objects/Customer", token=kenji_token)
    assert status == 200, customers
    assert len(customers) > 0
    for customer in customers:
        assert customer["email"] is None, customer
        assert customer["lifetime_value"] is None, customer
        assert set(customer["_maskedFields"]) == {"email", "lifetime_value"}, customer
        assert customer["name"], customer  # non-confidential fields untouched
        assert customer["country"], customer


def test_lineage_endpoint_is_governed_by_the_same_pdp(alice_token: str, jdoe_token: str) -> None:
    status, body = _request("GET", f"{KNOWLEDGE}/lineage/hl:{TENANT_ID}:demo:object-type:Customer", token=alice_token)
    assert status == 403, body

    status, _ = _request("GET", f"{KNOWLEDGE}/lineage/hl:{TENANT_ID}:demo:object-type:Customer", token=jdoe_token)
    assert status == 200
