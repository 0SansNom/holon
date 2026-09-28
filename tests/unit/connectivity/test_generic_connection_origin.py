"""Unit tests for REST connection origin pinning."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.modules.setdefault("asyncpg", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app.generic_source_registry import (  # noqa: E402
    SourceConfigError,
    _assert_connection_origin,
    _normalize_origin,
)


def test_normalize_origin_strips_path_and_lowercases_host() -> None:
    assert _normalize_origin("https://API.HubAPI.com/crm/v3?x=1") == "https://api.hubapi.com"
    assert _normalize_origin("http://reviews-api:8000/reviews.json") == "http://reviews-api:8000"


@pytest.mark.parametrize("value", ["", "api.example.com", "ftp://api.example.com", "https://"])
def test_normalize_origin_rejects_non_http_origins(value: str) -> None:
    with pytest.raises(SourceConfigError):
        _normalize_origin(value)


def test_connection_origin_allows_same_origin_paths() -> None:
    _assert_connection_origin("c", "https://api.example.com", "https://api.example.com/v1/items")


@pytest.mark.parametrize(
    "base_url",
    [
        "https://attacker.example/steal",
        "http://api.example.com/v1",  # scheme downgrade
        "https://api.example.com:8443/v1",  # other port
        "https://api.example.com.attacker.example/v1",
    ],
)
def test_connection_origin_rejects_other_origins(base_url: str) -> None:
    with pytest.raises(SourceConfigError, match="allowed_origin"):
        _assert_connection_origin("c", "https://api.example.com", base_url)


def test_connection_without_origin_is_refused() -> None:
    with pytest.raises(SourceConfigError, match="no allowed_origin"):
        _assert_connection_origin("legacy", None, "https://api.example.com/v1")
