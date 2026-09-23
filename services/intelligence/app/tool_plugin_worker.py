"""Subprocess entry for sandboxed agent tool plugin invokes.

Stdin: JSON ``{"entry_point": "...", "tool_input": {...}}``
Stdout: JSON ``{"ok": true, "result": {...}}`` or ``{"ok": false, "error": "..."}``
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from typing import Any


def run_invoke(entry_point: str, tool_input: dict) -> dict:
    """Load plugin and run ``invoke`` (sync wrapper around async plugins)."""
    from holon_common.plugin import load_entry_point

    plugin = load_entry_point(entry_point)
    result = plugin.invoke(tool_input)
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    if not isinstance(result, dict):
        raise TypeError(f"plugin invoke must return dict, got {type(result)!r}")
    return result


def main(argv: list[str] | None = None) -> int:
    del argv  # reserved
    try:
        raw = sys.stdin.buffer.read()
        request = json.loads(raw.decode())
        entry_point = request["entry_point"]
        tool_input = request.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            raise TypeError("tool_input must be a dict")
        result = run_invoke(entry_point, tool_input)
        sys.stdout.write(json.dumps({"ok": True, "result": result}, default=str))
        sys.stdout.flush()
        return 0
    except Exception as exc:
        sys.stdout.write(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()},
                default=str,
            )
        )
        sys.stdout.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
