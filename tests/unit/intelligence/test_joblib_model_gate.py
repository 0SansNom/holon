"""Tests for Joblib Model Gate."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.modules.setdefault("asyncpg", MagicMock())
sys.modules.setdefault("joblib", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "intelligence"))

from app.model_registry import joblib_models_allowed  # noqa: E402


def test_joblib_allowed_by_default_outside_production(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_ENV", raising=False)
    monkeypatch.delenv("HOLON_ALLOW_JOBLIB_MODELS", raising=False)
    assert joblib_models_allowed() is True


def test_joblib_refused_in_production_unless_forced(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_ENV", "production")
    monkeypatch.delenv("HOLON_ALLOW_JOBLIB_MODELS", raising=False)
    assert joblib_models_allowed() is False
    monkeypatch.setenv("HOLON_ALLOW_JOBLIB_MODELS", "true")
    assert joblib_models_allowed() is True
