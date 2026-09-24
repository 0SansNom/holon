"""Unit tests for agent tool plugin subprocess isolation."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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
os.environ["HOLON_TOOL_PLUGIN_ENTRY_PREFIXES"] = "holon_test_plugins.,app.tool_plugins.,app.plugins."

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("httpx", MagicMock())
sys.modules.setdefault("asyncpg", MagicMock())

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "services" / "intelligence"))
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "tests" / "fixtures" / "plugins"))

from app import tool_plugin_sandbox  # noqa: E402
from app.tool_plugin_sandbox import PluginSandboxError, invoke_tool_plugin  # noqa: E402

PLUGIN_DIR = REPO / "tests" / "fixtures" / "plugins" / "holon_test_plugins"


class _EchoPlugin:
    async def invoke(self, tool_input: dict) -> dict:
        return {"echo": tool_input.get("x"), "pid": os.getpid()}


def test_isolation_mode_defaults_to_subprocess(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_TOOL_PLUGIN_ISOLATION", raising=False)
    assert tool_plugin_sandbox.isolation_mode() == "subprocess"


def test_inprocess_invoke(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_TOOL_PLUGIN_ISOLATION", "inprocess")
    monkeypatch.setattr("holon_common.plugin.load_entry_point", lambda ep: _EchoPlugin())
    parent_pid = os.getpid()
    result = asyncio.run(invoke_tool_plugin("holon_test_plugins.x:Y", {"x": 1}))
    assert result["echo"] == 1
    assert result["pid"] == parent_pid


def test_subprocess_invoke_isolated_pid(monkeypatch) -> None:
    path = PLUGIN_DIR / "_sandbox_echo_plugin.py"
    path.write_text(
        "import os\n"
        "from holon_common.plugin import PluginManifest\n\n"
        "class SandboxEchoPlugin:\n"
        "    manifest = PluginManifest(\n"
        '        name="sandbox-echo", version="1.0.0", plugin_type="agent_tool",\n'
        '        tool_name="sandbox_echo", tool_description="echo",\n'
        '        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},\n'
        '        risk_level="low",\n'
        '        entry_point="holon_test_plugins._sandbox_echo_plugin:SandboxEchoPlugin",\n'
        "    )\n"
        "    async def invoke(self, tool_input):\n"
        '        return {"echo": tool_input.get("x"), "pid": os.getpid()}\n'
    )
    monkeypatch.setenv("HOLON_TOOL_PLUGIN_ISOLATION", "subprocess")
    monkeypatch.setenv("HOLON_TOOL_PLUGIN_TIMEOUT_SECONDS", "10")
    # Worker inherits PYTHONPATH from parent — ensure fixture plugins resolve.
    os.environ["PYTHONPATH"] = os.pathsep.join(
        [
            str(REPO / "services" / "intelligence"),
            str(REPO / "libs"),
            str(REPO / "tests" / "fixtures" / "plugins"),
            os.environ.get("PYTHONPATH", ""),
        ]
    )
    try:
        result = asyncio.run(
            invoke_tool_plugin(
                "holon_test_plugins._sandbox_echo_plugin:SandboxEchoPlugin",
                {"x": "hi"},
            )
        )
        assert result["echo"] == "hi"
        assert result["pid"] != os.getpid()
    finally:
        path.unlink(missing_ok=True)


def test_subprocess_timeout(monkeypatch) -> None:
    path = PLUGIN_DIR / "_sandbox_hang_plugin.py"
    path.write_text(
        "import time\n"
        "from holon_common.plugin import PluginManifest\n\n"
        "class SandboxHangPlugin:\n"
        "    manifest = PluginManifest(\n"
        '        name="sandbox-hang", version="1.0.0", plugin_type="agent_tool",\n'
        '        tool_name="sandbox_hang", tool_description="hang",\n'
        '        input_schema={"type": "object", "properties": {}},\n'
        '        risk_level="low",\n'
        '        entry_point="holon_test_plugins._sandbox_hang_plugin:SandboxHangPlugin",\n'
        "    )\n"
        "    async def invoke(self, tool_input):\n"
        "        time.sleep(30)\n"
        "        return {}\n"
    )
    monkeypatch.setenv("HOLON_TOOL_PLUGIN_ISOLATION", "subprocess")
    monkeypatch.setenv("HOLON_TOOL_PLUGIN_TIMEOUT_SECONDS", "1")
    os.environ["PYTHONPATH"] = os.pathsep.join(
        [
            str(REPO / "services" / "intelligence"),
            str(REPO / "libs"),
            str(REPO / "tests" / "fixtures" / "plugins"),
            os.environ.get("PYTHONPATH", ""),
        ]
    )
    try:
        with pytest.raises(PluginSandboxError, match="exceeded"):
            asyncio.run(
                invoke_tool_plugin(
                    "holon_test_plugins._sandbox_hang_plugin:SandboxHangPlugin",
                    {},
                )
            )
    finally:
        path.unlink(missing_ok=True)


def test_worker_main_reports_failure(capsys, monkeypatch) -> None:
    from app import tool_plugin_worker

    bad = json.dumps({"entry_point": "holon_test_plugins.missing:Nope", "tool_input": {}}).encode()

    class _Buf:
        @staticmethod
        def read() -> bytes:
            return bad

    class _Stdin:
        buffer = _Buf()

    monkeypatch.setattr(sys, "stdin", _Stdin())
    code = tool_plugin_worker.main()
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "error" in payload
