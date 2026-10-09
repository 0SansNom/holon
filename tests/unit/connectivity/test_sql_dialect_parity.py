"""SQL dialect names and default ports stay aligned across Python and the UI.

The connectivity image and the web bundle do not share a module at runtime,
so this reads the TypeScript sources and compares them to
``sql_drivers.VALID_DIALECTS`` and ``DEFAULT_PORTS``.
"""

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

pytestmark = pytest.mark.unit

_API = REPO / "services/experience/web/src/api/connectivity.ts"
_DIALOG = REPO / "services/experience/web/src/components/Sources/SqlConnectionDialog.tsx"

_UNION = re.compile(r"export type SqlDialect\s*=\s*([^;]*);")
_NUMBER_RECORD = re.compile(
    r"const DEFAULT_PORTS:\s*Record<SqlDialect,\s*number>\s*=\s*\{([^}]*)\}",
    re.S,
)
_LABEL_RECORD = re.compile(
    r"const DIALECT_LABELS:\s*Record<SqlDialect,\s*string>\s*=\s*\{([^}]*)\}",
    re.S,
)
_QUOTED = re.compile(r'"([a-z][a-z0-9_]*)"')
_PORT = re.compile(r"^\s*([a-z][a-z0-9_]*)\s*:\s*(\d+)\s*,?\s*$", re.M)
_LABEL = re.compile(r'^\s*([a-z][a-z0-9_]*)\s*:\s*"([^"]*)"\s*,?\s*$', re.M)


def _must(pattern: re.Pattern[str], text: str, what: str) -> str:
    match = pattern.search(text)
    if match is None:
        raise AssertionError(f"{what} not found")
    return match.group(1)


def test_frontend_sql_dialects_match_drivers() -> None:
    api = _API.read_text()
    dialog = _DIALOG.read_text()

    union = set(_QUOTED.findall(_must(_UNION, api, "SqlDialect union")))
    ports = {name: int(port) for name, port in _PORT.findall(_must(_NUMBER_RECORD, dialog, "DEFAULT_PORTS"))}
    labels = dict(_LABEL.findall(_must(_LABEL_RECORD, dialog, "DIALECT_LABELS")))

    assert set(sql_drivers.DEFAULT_PORTS) == sql_drivers.VALID_DIALECTS
    assert union == sql_drivers.VALID_DIALECTS
    assert ports == dict(sql_drivers.DEFAULT_PORTS)
    assert set(labels) == sql_drivers.VALID_DIALECTS
    assert all(label.strip() for label in labels.values())
