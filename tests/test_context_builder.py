"""Context Builder: one question per retrieval channel
(structural lookup, structural aggregation, semantic fallback), proving
ordering (structural first, semantic only when structural
resolution finds nothing) actually holds. Requires the
stack running (`make up`).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

# Real, metered Anthropic/Voyage calls — excluded from CI by default (cost +
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


def test_structural_lookup_never_touches_the_semantic_channel(jdoe_token: str) -> None:
    status, body = _request("POST", f"{INTELLIGENCE}/ask", token=jdoe_token, body={"query": "Tell me about customer 2"})
    assert status == 200, body
    assert body["intent"] == "lookup", body
    assert body["channels_used"] == ["structural"], body
    assert "Customer/2" in body["citations"], body
    assert body["grounded"] is True, body


def test_structural_aggregation_uses_the_new_count_operator(jdoe_token: str) -> None:
    status, body = _request(
        "POST", f"{INTELLIGENCE}/ask", token=jdoe_token, body={"query": "How many SupportTicket records have status open?"}
    )
    assert status == 200, body
    assert body["intent"] == "aggregation", body
    assert body["channels_used"] == ["structural"], body
    assert any(c.startswith("plan:") for c in body["citations"]), body
    assert body["grounded"] is True, body


def test_unresolved_entity_falls_back_to_semantic(jdoe_token: str) -> None:
    status, body = _request(
        "POST", f"{INTELLIGENCE}/ask", token=jdoe_token, body={"query": "What does encours mean in this system?"}
    )
    assert status == 200, body
    assert body["channels_used"] == ["semantic"], body
    assert any(c.startswith("glossary:") for c in body["citations"]), body
