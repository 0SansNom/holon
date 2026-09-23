"""Unit tests for agent transcript → LLM message rebuild."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Intelligence modules read env at import time.
os.environ.setdefault("HOLON_TENANT_ID", "acme")
os.environ.setdefault("HOLON_WORKSPACE_ID", "main")
os.environ.setdefault("HOLON_JWT_SECRET", "unit-test-secret")
os.environ.setdefault("HOLON_KNOWLEDGE_URL", "http://knowledge:8000")
os.environ.setdefault("HOLON_SPICEDB_URL", "http://spicedb:50051")
os.environ.setdefault("HOLON_SPICEDB_PRESHARED_KEY", "unit")
os.environ.setdefault("HOLON_OPA_URL", "http://opa:8181")
os.environ.setdefault("HOLON_DB_URL", "postgresql://unused")
os.environ.setdefault("HOLON_KAFKA_BOOTSTRAP", "unused:9092")
os.environ.setdefault("HOLON_QDRANT_URL", "http://qdrant:6333")

# Host pytest: stub heavy deps imported at module load.
sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("httpx", MagicMock())
sys.modules.setdefault("asyncpg", MagicMock())

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "services" / "intelligence"))
sys.path.insert(0, str(REPO / "libs"))

from app.agent_runtime import messages_from_transcript  # noqa: E402


def test_messages_from_transcript_folds_tool_results() -> None:
    transcript = [
        {"role": "user", "content": {"text": "hold customer 1"}, "recorded_at": None},
        {
            "role": "assistant",
            "content": {
                "content_blocks": [
                    {"type": "tool_use", "id": "t1", "name": "Customer_putOnCreditHold", "input": {}},
                ]
            },
            "recorded_at": None,
        },
        {
            "role": "tool",
            "content": {"tool_use_id": "t1", "result": {"status_code": 200, "body": {"ok": True}}},
            "recorded_at": None,
        },
        {
            "role": "assistant",
            "content": {"content_blocks": [{"type": "text", "text": "done"}]},
            "recorded_at": None,
        },
        {"role": "user", "content": {"text": "thanks"}, "recorded_at": None},
    ]
    messages = messages_from_transcript(transcript)
    assert messages[0] == {"role": "user", "content": "hold customer 1"}
    assert messages[1]["role"] == "assistant"
    assert messages[2]["role"] == "user"
    assert messages[2]["content"][0]["type"] == "tool_result"
    assert messages[2]["content"][0]["tool_use_id"] == "t1"
    assert json.loads(messages[2]["content"][0]["content"])["status_code"] == 200
    assert messages[3] == {"role": "assistant", "content": [{"type": "text", "text": "done"}]}
    assert messages[4] == {"role": "user", "content": "thanks"}
