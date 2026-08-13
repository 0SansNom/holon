"""LLM Gateway — a real call to a real model, not a stubbed-out demo.

The `LLMClient` protocol keeps the Gateway provider-agnostic (same
reasoning as `holon_common.authz.PermissionClient` not hand-tying itself
to one vendor's SDK conventions): `AnthropicClient` is the only
implementation today, but swapping providers later means adding a
class, not touching every caller.

Resilience matches every other outbound call in this build: explicit
timeout, retry with backoff+jitter for transient errors, a circuit
breaker so a struggling provider fails fast instead of piling up slow
timeouts across concurrent agent turns.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

import anthropic

from holon_common import CircuitBreaker, retry_with_backoff

logger = logging.getLogger("intelligence.llm_gateway")

_TIMEOUT_SECONDS = 30.0


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str
    # Raw content blocks (Anthropic's `messages.create` response content,
    # as plain dicts) — carries both `.text` and raw `tool_use` blocks.
    content_blocks: list[dict] = field(default_factory=list)


class LLMClient(Protocol):
    async def complete(
        self, *, system: str, messages: list[dict], max_tokens: int, tools: Optional[list[dict]] = None
    ) -> LLMResponse: ...


class AnthropicClient:
    def __init__(self, api_key: str, model: str):
        self._model = model
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=_TIMEOUT_SECONDS)
        self._breaker = CircuitBreaker(name="anthropic-llm", failure_threshold=5, cooldown_seconds=30.0)

    async def complete(
        self, *, system: str, messages: list[dict], max_tokens: int, tools: Optional[list[dict]] = None
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {"model": self._model, "system": system, "messages": messages, "max_tokens": max_tokens}
        if tools:
            kwargs["tools"] = tools

        async def _do() -> LLMResponse:
            response = await retry_with_backoff(
                lambda: self._client.messages.create(**kwargs),
                attempts=3,
                base_delay=2.0,
                retry_on=(anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError),
                what="Anthropic completion",
            )
            text = "".join(block.text for block in response.content if block.type == "text")
            return LLMResponse(
                text=text,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                stop_reason=response.stop_reason or "unknown",
                content_blocks=[block.model_dump() for block in response.content],
            )

        return await self._breaker.call(_do)


class FakeLLMClient:
    """Deterministic stand-in for CI / disabled Intelligence — no network, no spend."""

    async def complete(
        self, *, system: str, messages: list[dict], max_tokens: int, tools: Optional[list[dict]] = None
    ) -> LLMResponse:
        last_user = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                content = message.get("content", "")
                last_user = content if isinstance(content, str) else str(content)
                break
        urns = re.findall(r"\[URN:\s*([^\]]+)\]", last_user)
        if urns:
            text = f"Answer grounded on retrieved context [URN: {urns[0]}]."
        elif "(no context retrieved)" in last_user:
            text = "I don't have enough context to answer."
        else:
            text = f"[fake-llm] {last_user[:200]}" if last_user else "[fake-llm] ok"
        return LLMResponse(
            text=text,
            input_tokens=len(system) // 4 + 1,
            output_tokens=len(text) // 4 + 1,
            stop_reason="end_turn",
            content_blocks=[{"type": "text", "text": text}],
        )


def build_llm_client() -> LLMClient:
    """Provider dispatch. `fake` needs no API key (CI / flag-off stacks)."""
    provider = os.environ.get("HOLON_LLM_PROVIDER", "anthropic").strip().lower() or "anthropic"
    if provider == "fake":
        logger.info("using FakeLLMClient (HOLON_LLM_PROVIDER=fake)")
        return FakeLLMClient()
    if provider != "anthropic":
        raise RuntimeError(f"unsupported HOLON_LLM_PROVIDER={provider!r} (supported: anthropic, fake)")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key or api_key == "change-me":
        raise RuntimeError(
            "ANTHROPIC_API_KEY is required when HOLON_LLM_PROVIDER=anthropic "
            "(set HOLON_LLM_PROVIDER=fake for CI / no-spend)"
        )
    model = os.environ.get("HOLON_LLM_MODEL", "claude-sonnet-5")
    return AnthropicClient(api_key, model)
