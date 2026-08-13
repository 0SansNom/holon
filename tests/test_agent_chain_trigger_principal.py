"""Unit tests — agent chain trigger principal (no stack)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parent.parent
sys.modules.setdefault("httpx", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "automation"))

from app.agent_chain_trigger import chain_agent_local_name, chain_agent_principal  # noqa: E402


def test_chain_agent_defaults_to_ingest_bot(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_CHAIN_TRIGGER_AGENT_LOCAL_NAME", raising=False)
    assert chain_agent_local_name() == "ingest-bot"
    p = chain_agent_principal("acme", on_behalf_of="hl:acme:global:user:jdoe")
    assert p.urn == "hl:acme:global:agent:ingest-bot"
    assert p.type == "agent"
    assert p.on_behalf_of == "hl:acme:global:user:jdoe"


def test_chain_agent_local_name_override(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_CHAIN_TRIGGER_AGENT_LOCAL_NAME", "custom-bot")
    p = chain_agent_principal("acme")
    assert p.urn == "hl:acme:global:agent:custom-bot"
