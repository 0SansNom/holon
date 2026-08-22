"""Unit tests for AirbyteClient (no stack).

PROVISIONAL, unlike the rest of this codebase's "real end-to-end wait,
not mocked" testing philosophy: no shared Airbyte dev instance exists
yet (ADR 027 defers provisioning one), so there is nothing real to hit.
These tests assert the request shape against Airbyte's documented
Public API contract via `httpx.MockTransport`, not a live server. Once
`HOLON_AIRBYTE_API_URL` points at a real shared instance, add a real
integration test under `tests/integration/connectivity/` alongside
these (or in place of the ones that become redundant) — do not treat
this file as the permanent test for this module.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import httpx

_MODULE_PATH = Path(__file__).resolve().parents[3] / "services" / "connectivity" / "app" / "airbyte_client.py"
_spec = importlib.util.spec_from_file_location("connectivity_airbyte_client", _MODULE_PATH)
airbyte_client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(airbyte_client)
AirbyteApiError = airbyte_client.AirbyteApiError
AirbyteClient = airbyte_client.AirbyteClient


def _client(handler) -> AirbyteClient:
    return AirbyteClient(
        base_url="https://airbyte.test",
        client_id="test-client-id",
        client_secret="test-client-secret",
        transport=httpx.MockTransport(handler),
    )


def test_create_source_posts_to_public_api_and_returns_source_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/public/v1/applications/token":
            return httpx.Response(200, json={"access_token": "fake-token"})
        if request.url.path == "/api/public/v1/sources":
            return httpx.Response(200, json={"sourceId": "src-123"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = _client(handler)
    source_id = asyncio.run(
        client.create_source(name="holon-acme-orders", workspace_id="ws-1", configuration={"host": "db"})
    )

    assert source_id == "src-123"
    token_request, source_request = seen
    assert token_request.method == "POST"
    assert source_request.method == "POST"
    payload = json.loads(source_request.content)
    assert payload == {"name": "holon-acme-orders", "workspaceId": "ws-1", "configuration": {"host": "db"}}
    assert source_request.headers["Authorization"] == "Bearer fake-token"


def test_trigger_sync_posts_connection_id_and_job_type() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/public/v1/applications/token":
            return httpx.Response(200, json={"access_token": "fake-token"})
        if request.url.path == "/api/public/v1/jobs":
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"jobId": 42})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = _client(handler)
    job_id = asyncio.run(client.trigger_sync("conn-1"))

    assert job_id == "42"
    assert captured["body"] == {"connectionId": "conn-1", "jobType": "sync"}


def test_non_2xx_response_raises_airbyte_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/public/v1/applications/token":
            return httpx.Response(200, json={"access_token": "fake-token"})
        return httpx.Response(400, text="bad request")

    client = _client(handler)
    try:
        asyncio.run(client.trigger_sync("conn-1"))
        raise AssertionError("expected AirbyteApiError")
    except AirbyteApiError as exc:
        assert "400" in str(exc)
