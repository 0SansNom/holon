"""Tenant-scoped Iceberg table identifiers.

Dataset local names are shared across filiales (`customers`). Tables live
in one warehouse namespace (`raw`); the tenant is encoded into the table
name so two orgs cannot overwrite or read each other's snapshots.
"""

from __future__ import annotations

import re

NAMESPACE = "raw"

# Provisioning tenant ids allow hyphen; Iceberg table names do not.
_TENANT_INPUT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_TABLE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_ENCODED_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def iceberg_tenant_prefix(tenant_id: str) -> str:
    """Injective sanitization: `_` and `-` are both legal in tenant ids
    (provisioning) but Iceberg table names only allow `[A-Za-z0-9_]`.
    Encode them distinctly so `fil-iale` and `fil_iale` never collide.
    """
    if not tenant_id or not tenant_id.strip():
        raise ValueError("tenant_id is required for Iceberg table identifiers")
    tenant_id = tenant_id.strip()
    if not _TENANT_INPUT_RE.fullmatch(tenant_id):
        raise ValueError(
            f"invalid tenant_id {tenant_id!r} — must match [A-Za-z][A-Za-z0-9_-]*"
        )
    encoded = tenant_id.replace("_", "_u").replace("-", "_d")
    if not _ENCODED_RE.fullmatch(encoded):
        raise ValueError(f"invalid tenant_id {tenant_id!r} after encoding")
    return encoded


def iceberg_table_identifier(tenant_id: str, table_name: str) -> tuple[str, str]:
    if not table_name or not str(table_name).strip():
        raise ValueError("table_name is required for Iceberg table identifiers")
    table_name = str(table_name).strip()
    if not _TABLE_NAME_RE.fullmatch(table_name):
        raise ValueError(
            f"invalid table_name {table_name!r} — Iceberg names must match [A-Za-z][A-Za-z0-9_]*"
        )
    return (NAMESPACE, f"{iceberg_tenant_prefix(tenant_id)}__{table_name}")


def iceberg_legacy_identifier(table_name: str) -> tuple[str, str]:
    """Pre-tenant-prefix identifier (`raw.<table_name>`). Used as a read
    alias and as the source of a catalog rename on first write after upgrade.
    """
    if not table_name or not str(table_name).strip():
        raise ValueError("table_name is required for Iceberg table identifiers")
    table_name = str(table_name).strip()
    if not _TABLE_NAME_RE.fullmatch(table_name):
        raise ValueError(
            f"invalid table_name {table_name!r} — Iceberg names must match [A-Za-z][A-Za-z0-9_]*"
        )
    return (NAMESPACE, table_name)


def iceberg_read_identifiers(tenant_id: str, table_name: str) -> tuple[tuple[str, str], ...]:
    """Prefixed identifier first, then the unprefixed legacy alias."""
    current = iceberg_table_identifier(tenant_id, table_name)
    legacy = iceberg_legacy_identifier(table_name)
    if current == legacy:
        return (current,)
    return (current, legacy)
