"""Prefix allowlist for Intelligence tool-plugin entry points."""

from holon_common.plugin import PluginConflictError
from holon_common.plugin import assert_entry_point_allowed as _assert_entry_point_allowed
from holon_common.plugin import entry_prefixes

__all__ = ["assert_entry_point_allowed", "entry_prefixes"]


def assert_entry_point_allowed(entry_point: str) -> None:
    try:
        _assert_entry_point_allowed(entry_point)
    except PluginConflictError as exc:
        raise ValueError(str(exc)) from exc
