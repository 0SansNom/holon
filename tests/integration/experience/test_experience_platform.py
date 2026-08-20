"""Tests for Experience Platform."""

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
    assert "default_user_urn" not in body
    assert "customer_object_type_urn" not in body
    assert "intelligence_enabled" in body
    assert isinstance(body["intelligence_enabled"], bool)


def test_experience_lineage_proxy(jdoe_token: str):
    """Verify the proxied Knowledge lineage endpoint."""
    status, lineage = _request(
        "GET",
        f"{EXPERIENCE}/api/lineage/hl:{TENANT_ID}:main:object-type:Customer",
        token=jdoe_token,
    )
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
