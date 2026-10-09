"""Source kind table used by sync resolution and the scheduler."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app import source_kinds as mod  # noqa: E402
from app.source_kinds import SOURCE_KINDS, resolve_registered_source  # noqa: E402

pytestmark = pytest.mark.unit


def _with_kinds(patched: tuple) -> None:
    """Replace SOURCE_KINDS for the duration of a resolve test."""
    mod.SOURCE_KINDS = patched


def test_source_kinds_cover_five_registered_families() -> None:
    assert [kind.name for kind in SOURCE_KINDS] == [
        "generic_rest",
        "sql",
        "object",
        "sftp",
        "salesforce",
    ]


def test_connector_local_names_match_previous_urn_prefixes() -> None:
    by_name = {kind.name: kind for kind in SOURCE_KINDS}
    assert by_name["generic_rest"].connector_local_name("reviews") == "generic-rest-reviews"
    assert by_name["sql"].connector_local_name("orders") == "sql-orders"
    assert by_name["object"].connector_local_name("files") == "object-files"
    assert by_name["sftp"].connector_local_name("feeds") == "sftp-feeds"
    assert by_name["salesforce"].connector_local_name("leads") == "salesforce-leads"


def test_uses_append_follows_cursor_or_incremental_field() -> None:
    by_name = {kind.name: kind for kind in SOURCE_KINDS}
    assert by_name["generic_rest"].uses_append({"cursor_property": "updated_at"}) is True
    assert by_name["generic_rest"].uses_append({"cursor_property": None}) is False
    assert by_name["sql"].uses_append({"cursor_property": "id"}) is True
    assert by_name["salesforce"].uses_append({"cursor_property": "SystemModstamp"}) is True
    assert by_name["object"].uses_append({"incremental": True}) is True
    assert by_name["object"].uses_append({"incremental": False}) is False
    assert by_name["sftp"].uses_append({"incremental": True}) is True
    assert by_name["sftp"].uses_append({"incremental": False}) is False


def test_resolve_registered_source_returns_first_matching_kind() -> None:
    original = mod.SOURCE_KINDS
    patched = []
    for i, kind in enumerate(original):
        get_source = AsyncMock(return_value=None)
        if i == 2:
            get_source = AsyncMock(return_value={"name": "files", "status": "active", "incremental": True})
        patched.append(
            mod.SourceKind(
                name=kind.name,
                get_source=get_source,
                fetch_for_dataset=kind.fetch_for_dataset,
                list_all_scheduled=kind.list_all_scheduled,
                connector_local_name=kind.connector_local_name,
                uses_append=kind.uses_append,
            )
        )
    _with_kinds(tuple(patched))
    try:
        resolved = asyncio.run(resolve_registered_source(MagicMock(), "acme", "files"))
        assert resolved is not None
        kind, row = resolved
        assert kind.name == "object"
        assert row["name"] == "files"
        patched[0].get_source.assert_awaited_once()
        patched[1].get_source.assert_awaited_once()
        patched[2].get_source.assert_awaited_once()
        patched[3].get_source.assert_not_awaited()
    finally:
        _with_kinds(original)


def test_resolve_registered_source_returns_none_when_unknown() -> None:
    original = mod.SOURCE_KINDS
    patched = tuple(
        mod.SourceKind(
            name=kind.name,
            get_source=AsyncMock(return_value=None),
            fetch_for_dataset=kind.fetch_for_dataset,
            list_all_scheduled=kind.list_all_scheduled,
            connector_local_name=kind.connector_local_name,
            uses_append=kind.uses_append,
        )
        for kind in original
    )
    _with_kinds(patched)
    try:
        assert asyncio.run(resolve_registered_source(MagicMock(), "acme", "missing")) is None
    finally:
        _with_kinds(original)
