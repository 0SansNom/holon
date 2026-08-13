"""Unit tests — FakeLLMClient (host-side, no stack / no real deps)."""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parent.parent

# Intelligence app imports anthropic/httpx at module load; stub them for host pytest.
sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("httpx", MagicMock())
sys.modules.setdefault("asyncpg", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "intelligence"))

from app.llm_gateway import FakeLLMClient, build_llm_client  # noqa: E402


def test_fake_llm_complete_is_deterministic() -> None:
    client = FakeLLMClient()
    response = asyncio.run(
        client.complete(system="sys", messages=[{"role": "user", "content": "hello customer"}], max_tokens=64)
    )
    assert response.text.startswith("[fake-llm]")
    assert "hello customer" in response.text
    assert response.stop_reason == "end_turn"
    assert response.input_tokens >= 1
    assert response.output_tokens >= 1


def test_fake_llm_cites_context_urns() -> None:
    client = FakeLLMClient()
    response = asyncio.run(
        client.complete(
            system="sys",
            messages=[
                {
                    "role": "user",
                    "content": "Context:\n[URN: hl:acme:main:instance:Customer/1] Jane\n\n<user_query>\nWho?\n</user_query>",
                }
            ],
            max_tokens=64,
        )
    )
    assert "URN: hl:acme:main:instance:Customer/1" in response.text


def test_build_llm_client_fake_provider(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_LLM_PROVIDER", "fake")
    client = build_llm_client()
    assert isinstance(client, FakeLLMClient)


def test_build_llm_client_rejects_placeholder_anthropic_key(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "change-me")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_llm_client()


def test_hard_budget_caps_constant() -> None:
    # Import after stubs so agent_runtime's httpx import is the MagicMock above.
    from app.agent_runtime import HARD_BUDGET_CAPS  # noqa: WPS433

    assert HARD_BUDGET_CAPS["max_iterations"] == 20
    assert HARD_BUDGET_CAPS["max_tool_calls"] == 50
    assert HARD_BUDGET_CAPS["max_tokens"] == 100_000
