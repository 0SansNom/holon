"""Tests for SQL identifier quoting."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))

from holon_common.sql_ident import quote_identifier, require_identifier  # noqa: E402


def test_require_identifier_accepts_plain_and_qualified() -> None:
    assert require_identifier("orders") == "orders"
    assert require_identifier("public.orders") == "public.orders"


def test_require_identifier_rejects_injection() -> None:
    with pytest.raises(ValueError):
        require_identifier("orders; drop table x")
    with pytest.raises(ValueError):
        require_identifier('orders"')
    with pytest.raises(ValueError):
        require_identifier("")
    with pytest.raises(ValueError):
        require_identifier("1orders")


def test_quote_identifier_quotes_each_part() -> None:
    assert quote_identifier("orders") == '"orders"'
    assert quote_identifier("public.orders") == '"public"."orders"'
