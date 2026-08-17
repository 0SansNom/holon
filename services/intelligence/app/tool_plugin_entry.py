"""Prefix allowlist for Intelligence tool-plugin entry points."""

from __future__ import annotations

import os

# Default: only in-tree / package-prefixed entry points. Override with
# HOLON_TOOL_PLUGIN_ENTRY_PREFIXES=app.plugins.,my_vendor.
_DEFAULT_ENTRY_PREFIXES = ("app.plugins.", "app.tool_plugins.")


def entry_prefixes() -> tuple[str, ...]:
    raw = (os.environ.get("HOLON_TOOL_PLUGIN_ENTRY_PREFIXES") or "").strip()
    if not raw:
        return _DEFAULT_ENTRY_PREFIXES
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def assert_entry_point_allowed(entry_point: str) -> None:
    prefixes = entry_prefixes()
    if not any(entry_point.startswith(p) for p in prefixes):
        raise ValueError(
            f"tool plugin entry_point {entry_point!r} not under allowed prefixes {prefixes} "
            "(HOLON_TOOL_PLUGIN_ENTRY_PREFIXES)"
        )
