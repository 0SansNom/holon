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
    assert require_identifier("analytics.public.orders") == "analytics.public.orders"


def test_require_identifier_rejects_four_parts() -> None:
    with pytest.raises(ValueError):
        require_identifier("a.b.c.d")


@pytest.mark.parametrize("name", ["orders.", ".orders", "a..b", "a.b.", "a.1b.c", "orders\n", "a.b.c\n"])
def test_require_identifier_rejects_malformed_parts(name: str) -> None:
    with pytest.raises(ValueError):
        require_identifier(name)


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


def test_quote_identifier_dialects() -> None:
    assert quote_identifier("orders", dialect="postgres") == '"orders"'
    assert quote_identifier("public.orders", dialect="mysql") == "`public`.`orders`"
    assert quote_identifier("dbo.orders", dialect="mssql") == "[dbo].[orders]"
    # Unquoted so Snowflake folds to UPPERCASE (quoted names are case-sensitive).
    assert quote_identifier("public.orders", dialect="snowflake") == "public.orders"
    assert quote_identifier("ANALYTICS.ORDERS", dialect="snowflake") == "ANALYTICS.ORDERS"


@pytest.mark.parametrize(
    ("dialect", "one", "two", "three"),
    [
        ("postgres", '"orders"', '"public"."orders"', '"analytics"."public"."orders"'),
        ("mysql", "`orders`", "`public`.`orders`", "`analytics`.`public`.`orders`"),
        ("mssql", "[orders]", "[public].[orders]", "[analytics].[public].[orders]"),
        ("snowflake", "orders", "public.orders", "analytics.public.orders"),
    ],
)
def test_quote_identifier_one_two_three_parts(dialect: str, one: str, two: str, three: str) -> None:
    assert quote_identifier("orders", dialect=dialect) == one
    assert quote_identifier("public.orders", dialect=dialect) == two
    assert quote_identifier("analytics.public.orders", dialect=dialect) == three
    with pytest.raises(ValueError):
        quote_identifier("a.b.c.d", dialect=dialect)


def test_quote_identifier_rejects_unknown_dialect() -> None:
    with pytest.raises(ValueError):
        quote_identifier("orders", dialect="singlestore")
