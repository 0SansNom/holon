"""Integration: Intelligence RAG tenant filter + Knowledge lexical channel."""

from __future__ import annotations

from conftest import INTELLIGENCE, _request


def test_ask_glossary_falls_back_to_semantic_when_no_instance_hit(jdoe_token: str) -> None:
    """'encours' is glossary metadata — OpenSearch instances usually miss it,
    so the semantic channel (tenant-filtered Qdrant) should answer."""
    status, body = _request(
        "POST",
        f"{INTELLIGENCE}/ask",
        token=jdoe_token,
        body={"query": "What does encours mean in this system?"},
    )
    assert status == 200, body
    assert "semantic" in body.get("channels_used", []), body
    assert any(c.startswith("glossary:") for c in body.get("citations", [])), body


def test_ask_customer_name_can_use_lexical_channel(jdoe_token: str) -> None:
    """Free-text over instances goes through Knowledge /search (lexical)."""
    status, body = _request(
        "POST",
        f"{INTELLIGENCE}/ask",
        token=jdoe_token,
        body={"query": "Find customer records matching enterprise"},
    )
    assert status == 200, body
    channels = body.get("channels_used") or []
    # Lexical if OpenSearch has hits; otherwise semantic metadata is fine.
    assert channels, body
    assert set(channels) <= {"lexical", "structural", "semantic"}, body
