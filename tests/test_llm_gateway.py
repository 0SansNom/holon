"""Call through the LLM Gateway, proving the Gateway works end-to-end.
Requires the stack running (`make up`) with a real `ANTHROPIC_API_KEY` configured.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import IDENTITY, INTELLIGENCE, _request

# Real, metered Anthropic calls — excluded from CI by default (cost +
# secret-exposure risk); run explicitly with `pytest -m llm`.
pytestmark = pytest.mark.llm


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
