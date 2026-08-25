"""Tests for tenant-scoped Iceberg identifiers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))

from holon_common.iceberg_ident import (  # noqa: E402
    iceberg_legacy_identifier,
    iceberg_read_identifiers,
    iceberg_table_identifier,
)


def test_iceberg_identifier_namespaces_by_tenant() -> None:
    assert iceberg_table_identifier("acme", "customers") == ("raw", "acme__customers")
    assert iceberg_table_identifier("filiale", "customers") == ("raw", "filiale__customers")


def test_iceberg_identifier_hyphen_vs_underscore_tenants_do_not_collide() -> None:
    a = iceberg_table_identifier("fil-iale", "customers")
    b = iceberg_table_identifier("fil_iale", "customers")
    assert a != b


def test_empty_tenant_rejected() -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        iceberg_table_identifier("", "customers")
    with pytest.raises(ValueError, match="tenant_id"):
        iceberg_table_identifier("   ", "customers")


def test_illegal_characters_rejected() -> None:
    with pytest.raises(ValueError):
        iceberg_table_identifier("acme/prod", "customers")
    with pytest.raises(ValueError):
        iceberg_table_identifier("acme", "customers-v2")
    with pytest.raises(ValueError):
        iceberg_table_identifier("acme", "public.orders")


def test_table_name_with_double_underscore_is_allowed() -> None:
    ident = iceberg_table_identifier("acme", "foo__bar")
    assert ident == ("raw", "acme__foo__bar")
    assert ident != iceberg_table_identifier("acme", "foo")


def test_read_identifiers_include_legacy_unprefixed_alias() -> None:
    current, legacy = iceberg_read_identifiers("acme", "customers")
    assert current == ("raw", "acme__customers")
    assert legacy == iceberg_legacy_identifier("customers") == ("raw", "customers")
