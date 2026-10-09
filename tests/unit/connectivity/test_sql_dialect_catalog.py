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

_DIALOG_TSX = REPO / "services/experience/web/src/components/Sources/SqlConnectionDialog.tsx"


def _set_members(source: str, const_name: str) -> set[str]:
    match = re.search(
        rf"const {const_name} = new Set<SqlDialect>\(\[\n(.*?)\n\]\);",
        source,
        re.S,
    )
    assert match, f"{const_name} set not found"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_frontend_tls_defaults_match_python() -> None:
    """Names, ports and labels are covered by test_sql_dialect_parity."""
    assert _set_members(_DIALOG_TSX.read_text(), "TLS_BY_DEFAULT") == set(sql_drivers.TLS_BY_DEFAULT)


def test_wire_dialects_are_quote_dialects() -> None:
    for wire in sql_drivers.WIRE_DIALECTS:
        quote_identifier("orders", dialect=wire)
    with pytest.raises(ValueError):
        quote_identifier("orders", dialect="azure_synapse")
