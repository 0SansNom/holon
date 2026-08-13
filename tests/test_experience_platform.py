"""Dedicated test suite for Experience Platform (`services/experience/app/main.py`).

Covers:
- Probes (/health, /live, /ready)
- Platform Config (/api/config)
- Token Issuance Proxy (/api/token)
- Knowledge Proxy Endpoints (/api/customers, /api/lineage/..., /api/customers/{id}/orders, /api/customers/{id}/credit-hold)
- Application Builder Endpoints (/api/applications/{name}, draft/promote/render)
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

import pytest
from conftest import EXPERIENCE, IDENTITY, TENANT_ID, _request


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


def test_experience_probes():
    """Verify health, live, and ready endpoints."""
    for endpoint in ["/health", "/live", "/ready"]:
        status, body = _request("GET", f"{EXPERIENCE}{endpoint}")
        assert status == 200
        assert body == {"status": "ok"}


def test_experience_config():
    """Verify Experience platform configuration endpoint."""
    status, body = _request("GET", f"{EXPERIENCE}/api/config")
    assert status == 200
    assert body["tenant_id"] == TENANT_ID
    assert body["workspace_id"] == "main"
    assert "default_user_urn" in body
    assert "customer_object_type_urn" in body
    assert "allow_dev_login" in body
    assert "intelligence_enabled" in body
    assert isinstance(body["allow_dev_login"], bool)
    assert isinstance(body["intelligence_enabled"], bool)


def test_experience_token_proxy_is_limited_to_authenticated_self(jdoe_token: str):
    """The legacy proxy may refresh self, but cannot mint another identity."""
    user_urn = f"hl:{TENANT_ID}:global:user:jdoe"
    status, body = _request(
        "POST", f"{EXPERIENCE}/api/token", token=jdoe_token, body={"principal_urn": user_urn}
    )
    assert status == 200
    assert "access_token" in body
    assert body["token_type"] == "bearer"

    status, body = _request(
        "POST",
        f"{EXPERIENCE}/api/token",
        token=jdoe_token,
        body={"principal_urn": f"hl:{TENANT_ID}:global:user:msmith"},
    )
    assert status == 403, body


def test_experience_token_proxy_rejects_anonymous_callers():
    status, body = _request(
        "POST",
        f"{EXPERIENCE}/api/token",
        body={"principal_urn": f"hl:{TENANT_ID}:global:user:jdoe"},
    )
    assert status == 401, body


def test_experience_knowledge_proxies(jdoe_token: str):
    """Verify proxied Knowledge endpoints."""
    # List Customers
    status, customers = _request("GET", f"{EXPERIENCE}/api/customers", token=jdoe_token)
    assert status in (200, 404)  # 200 if seeded, 404 if empty

    # Get Lineage for Customer ObjectType URN
    status, lineage = _request(
        "GET",
        f"{EXPERIENCE}/api/lineage/hl:{TENANT_ID}:main:object-type:Customer",
        token=jdoe_token,
    )
    assert status in (200, 404)

    # Get Customer Orders proxy
    status, orders = _request("GET", f"{EXPERIENCE}/api/customers/1/orders", token=jdoe_token)
    assert status in (200, 404)


def test_experience_application_builder_lifecycle(jdoe_token: str):
    """Verify Application Builder draft creation, retrieval, promotion, and render via Experience."""
    app_name = f"test-app-{uuid.uuid4().hex[:8]}"
    definition = {
        "title": "Test Dashboard App",
        "description": "Integration test for Experience Application Builder",
        "components": [
            {
                "id": "customer-table",
                "type": "ObjectTable",
                "object_type": f"hl:{TENANT_ID}:main:object-type:Customer",
                "properties": ["name", "email", "credit_limit"],
            }
        ],
    }

    # 1. Create draft
    status, app_draft = _request(
        "POST",
        f"{EXPERIENCE}/api/applications/{app_name}",
        token=jdoe_token,
        body={"definition": definition},
    )
    assert status == 200
    assert app_draft["name"] == app_name
    assert app_draft["version"] == 1
    assert app_draft["status"] == "draft"

    # 2. Get application draft
    status, fetched = _request("GET", f"{EXPERIENCE}/api/applications/{app_name}", token=jdoe_token)
    assert status == 200
    assert fetched["name"] == app_name
    assert fetched["definition"]["title"] == "Test Dashboard App"

    # 3. Promote application
    status, promoted = _request("POST", f"{EXPERIENCE}/api/applications/{app_name}/promote", token=jdoe_token)
    assert status == 200
    assert promoted["status"] == "promoted"

    # 4. Fetch application data endpoint
    status, data = _request("GET", f"{EXPERIENCE}/api/applications/{app_name}/data", token=jdoe_token)
    assert status in (200, 404, 400)
