"""Out-of-process isolation for agent tool plugin ``invoke``.

Container RuntimeClass (gVisor) sandboxes the Intelligence process.
This module isolates *plugin code* from the agent loop: each invoke
runs in a short-lived subprocess with a hard timeout so a hung or
crashing plugin cannot take down the parent interpreter.

Modes (``HOLON_TOOL_PLUGIN_ISOLATION``):

- ``subprocess`` — default; production requires this
- ``inprocess`` — load + await in the parent (local debug only)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger("intelligence.tool_plugin_sandbox")

_DEFAULT_TIMEOUT = 15.0


def isolation_mode() -> str:
    raw = (os.environ.get("HOLON_TOOL_PLUGIN_ISOLATION") or "subprocess").strip().lower()
    return raw or "subprocess"


def invoke_timeout_seconds() -> float:
    raw = (os.environ.get("HOLON_TOOL_PLUGIN_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT
    try:
        return max(1.0, float(raw))
    except ValueError:
        return _DEFAULT_TIMEOUT


class PluginSandboxError(RuntimeError):
    """Plugin subprocess failed, timed out, or returned an invalid payload."""


async def invoke_tool_plugin(entry_point: str, tool_input: dict) -> dict:
    """Run plugin ``invoke`` under the configured isolation mode."""
    mode = isolation_mode()
    if mode == "inprocess":
        from holon_common.plugin import load_entry_point

        plugin = load_entry_point(entry_point)
        body = await plugin.invoke(tool_input)
        if not isinstance(body, dict):
            raise PluginSandboxError(f"plugin {entry_point!r} returned non-dict {type(body)!r}")
        return body
    if mode != "subprocess":
        raise PluginSandboxError(
            f"unsupported HOLON_TOOL_PLUGIN_ISOLATION={mode!r} (supported: subprocess, inprocess)"
        )
    return await _subprocess_invoke(entry_point, tool_input)


async def _subprocess_invoke(entry_point: str, tool_input: dict) -> dict:
    timeout = invoke_timeout_seconds()
    payload = json.dumps({"entry_point": entry_point, "tool_input": tool_input}, default=str).encode()
    env = os.environ.copy()
    # Keep PYTHONPATH / plugin prefixes; drop nothing critical — network plugins
    # (e.g. weather fixture) still need their feed URL. Isolation is process
    # boundary + timeout, not a full capability drop.
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.tool_plugin_worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(payload), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        try:
            await proc.communicate()
        except Exception:
            pass
        raise PluginSandboxError(
            f"plugin {entry_point!r} exceeded {timeout}s (HOLON_TOOL_PLUGIN_TIMEOUT_SECONDS)"
        ) from exc

    if proc.returncode != 0:
        err = (stderr or b"").decode(errors="replace")[:500]
        raise PluginSandboxError(
            f"plugin {entry_point!r} exited {proc.returncode}: {err or 'no stderr'}"
        )

    try:
        message = json.loads(stdout.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PluginSandboxError(f"plugin {entry_point!r} returned non-JSON stdout") from exc

    if not isinstance(message, dict) or not message.get("ok"):
        detail = message.get("error") if isinstance(message, dict) else message
        raise PluginSandboxError(f"plugin {entry_point!r} failed: {detail}")

    result = message.get("result")
    if not isinstance(result, dict):
        raise PluginSandboxError(f"plugin {entry_point!r} result must be a dict, got {type(result)!r}")
    return result


def sync_probe_worker(entry_point: str, tool_input: dict[str, Any]) -> dict:
    """Synchronous helper for unit tests (runs worker logic without asyncio parent)."""
    from . import tool_plugin_worker

    return tool_plugin_worker.run_invoke(entry_point, tool_input)
