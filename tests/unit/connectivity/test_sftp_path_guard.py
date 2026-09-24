"""Unit tests for SFTP remote path validation."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.modules.setdefault("asyncpg", MagicMock())
sys.modules.setdefault("paramiko", MagicMock())
# Avoid importing real pyarrow (host numpy can SIGBUS in this env).
sys.modules.setdefault("pyarrow", MagicMock())
sys.modules.setdefault("pyarrow.csv", MagicMock())
sys.modules.setdefault("pyarrow.json", MagicMock())
sys.modules.setdefault("pyarrow.parquet", MagicMock())
sys.modules.setdefault("pyarrow.lib", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app.sftp_source_registry import SourceConfigError, _require_remote_path  # noqa: E402


def test_accepts_plain_file_and_directory_paths() -> None:
    _require_remote_path("upload/suppliers.csv", what="remote_path")
    _require_remote_path("upload/landing", what="remote_prefix")
    _require_remote_path("/upload/landing/a.csv", what="remote_path")


def test_rejects_traversal_and_junk() -> None:
    with pytest.raises(SourceConfigError):
        _require_remote_path("../etc/passwd", what="remote_path")
    with pytest.raises(SourceConfigError):
        _require_remote_path("upload/../secret", what="remote_path")
    with pytest.raises(SourceConfigError):
        _require_remote_path("upload/foo;rm", what="remote_path")
    with pytest.raises(SourceConfigError):
        _require_remote_path("", what="remote_path")
