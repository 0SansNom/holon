"""Tests for Tool Plugin Entry Allowlist."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "intelligence"))

from app.tool_plugin_entry import (  # noqa: E402
    assert_entry_point_allowed,
    entry_prefixes,
)


def test_default_prefixes_allow_in_tree(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_TOOL_PLUGIN_ENTRY_PREFIXES", raising=False)
    assert entry_prefixes() == ("app.plugins.", "app.tool_plugins.")
    assert_entry_point_allowed("app.plugins.weather_lookup_plugin:WeatherLookupPlugin")


def test_default_prefixes_reject_arbitrary(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_TOOL_PLUGIN_ENTRY_PREFIXES", raising=False)
    with pytest.raises(ValueError, match="not under allowed prefixes"):
        assert_entry_point_allowed("os:system")
    with pytest.raises(ValueError, match="not under allowed prefixes"):
        assert_entry_point_allowed("evil.plugins.x:Y")


def test_custom_prefixes(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_TOOL_PLUGIN_ENTRY_PREFIXES", "vendor.tools.,app.plugins.")
    assert_entry_point_allowed("vendor.tools.foo:Bar")
    with pytest.raises(ValueError):
        assert_entry_point_allowed("app.tool_plugins.x:Y")
