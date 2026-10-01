"""Python dialect catalog stays aligned with the SQL connection dialog."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.modules.setdefault("asyncpg", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app import sql_drivers  # noqa: E402
from holon_common.sql_ident import quote_identifier  # noqa: E402

_DIALECT_TS = REPO / "services/experience/web/src/api/connectivity.ts"
_DIALOG_TSX = REPO / "services/experience/web/src/components/Sources/SqlConnectionDialog.tsx"


def _union_members(source: str, name: str) -> set[str]:
    match = re.search(rf"export type {name} =\n((?:[ \t]*\|[^\n]*\n)+)", source)
    assert match, f"{name} union not found"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _object_block(source: str, const_name: str) -> str:
    match = re.search(rf"const {const_name}[^=]*= \{{(.*?)\n\}};", source, re.S)
    assert match, f"{const_name} object not found"
    return match.group(1)


def _set_members(source: str, const_name: str) -> set[str]:
    match = re.search(
        rf"const {const_name} = new Set<SqlDialect>\(\[\n(.*?)\n\]\);",
        source,
        re.S,
    )
    assert match, f"{const_name} set not found"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_frontend_dialect_catalog_matches_python() -> None:
    dialog = _DIALOG_TSX.read_text()
    ports = {
        key: int(value)
        for key, value in re.findall(r"(\w+):\s*(\d+)", _object_block(dialog, "DEFAULT_PORTS"))
    }
    labels = set(re.findall(r"^\s*(\w+):", _object_block(dialog, "DIALECT_LABELS"), re.M))
    union = _union_members(_DIALECT_TS.read_text(), "SqlDialect")
    tls = _set_members(dialog, "TLS_BY_DEFAULT")

    assert union == set(sql_drivers.VALID_DIALECTS)
    assert set(ports) == set(sql_drivers.VALID_DIALECTS)
    assert ports == dict(sql_drivers.DEFAULT_PORTS)
    assert labels == set(sql_drivers.VALID_DIALECTS)
    assert tls == set(sql_drivers.TLS_BY_DEFAULT)


def test_wire_dialects_are_quote_dialects() -> None:
    for wire in sql_drivers.WIRE_DIALECTS:
        quote_identifier("orders", dialect=wire)
    with pytest.raises(ValueError):
        quote_identifier("orders", dialect="azure_synapse")
