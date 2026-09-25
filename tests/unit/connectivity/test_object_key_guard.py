"""Unit tests for object storage key / prefix shape guards."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.modules.setdefault("asyncpg", MagicMock())
sys.modules.setdefault("pyarrow", MagicMock())
sys.modules.setdefault("pyarrow.csv", MagicMock())
sys.modules.setdefault("pyarrow.fs", MagicMock())
sys.modules.setdefault("pyarrow.json", MagicMock())
sys.modules.setdefault("pyarrow.parquet", MagicMock())
sys.modules.setdefault("pyarrow.lib", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app.object_source_registry import SourceConfigError, _require_object_key  # noqa: E402


def test_object_key_accepts_plain_keys_and_trailing_slash_prefix() -> None:
    _require_object_key("landing/suppliers.csv", what="object_key")
    _require_object_key("landing/", what="key_prefix")
    _require_object_key("/landing/a.csv", what="object_key")


def test_object_key_rejects_traversal_and_junk() -> None:
    with pytest.raises(SourceConfigError):
        _require_object_key("../etc/passwd", what="object_key")
    with pytest.raises(SourceConfigError):
        _require_object_key("landing/../secret", what="key_prefix")
    with pytest.raises(SourceConfigError):
        _require_object_key("landing/foo;rm", what="object_key")
    with pytest.raises(SourceConfigError):
        _require_object_key("", what="object_key")
