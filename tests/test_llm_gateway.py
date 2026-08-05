"""Call through the LLM Gateway, proving the Gateway works end-to-end.
Requires the stack running (`make up`) with a real `ANTHROPIC_API_KEY` configured.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

# Real, metered Anthropic calls — excluded from CI by default (cost +
# secret-exposure risk); run explicitly with `pytest -m llm`.
pytestmark = pytest.mark.llm

IDENTITY = "http://localhost:8001"
INTELLIGENCE = "http://localhost:8006"

TENANT_ID = "acme"


def _request(method: str, url: str, *, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
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
    pytest.fail(f"could not mint a token for {principal_urn}")


@pytest.fixture(scope="session")
def jdoe_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:jdoe")


def test_intelligence_platform_is_up() -> None:
    status, body = _request("GET", f"{INTELLIGENCE}/health")
    assert status == 200
    assert body["status"] == "ok"


def test_a_real_llm_call_returns_a_non_empty_grounded_response(jdoe_token: str) -> None:
    status, body = _request("POST", f"{INTELLIGENCE}/ask", token=jdoe_token, body={"query": "Tell me about customer 1"})
    assert status == 200, body
    assert body["answer"].strip(), body
    assert body["tokens"]["input"] > 0, body
    assert body["tokens"]["output"] > 0, body
