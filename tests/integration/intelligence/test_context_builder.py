"""Tests for Context Builder."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import IDENTITY, INTELLIGENCE, _request

# Real, metered Anthropic/Voyage calls
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
