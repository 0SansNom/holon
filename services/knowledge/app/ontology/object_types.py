"""ObjectType core: self-serve creation and version history reads."""

from __future__ import annotations

import json
from typing import Optional

import asyncpg

from holon_common import Classification, most_restrictive

from . import definition_cache
from .urns import object_type_urn
from .lifecycle import (
    NON_DELETABLE_OBJECT_TYPE_STATUSES,
    normalize_deprecation_metadata,
)

INITIAL_CLASSIFICATION = "internal"

VALID_VISIBILITIES = frozenset({"prominent", "normal", "hidden"})


# All JSONB columns on `object_type`; a subset (without `column_classification`)
# covers `object_type_version`. Centralised so adding a new JSONB column only
# requires touching this constant — not the four independent call sites below.
_OT_JSONB_KEYS: tuple[str, ...] = (
    "property_mapping", "implements", "derived_properties", "markings",
    "property_formats", "conditional_formats", "property_types",
    "link_constraint_bindings", "interface_property_bindings", "column_classification",
)
# object_type_version has no column_classification column.
_OTV_JSONB_KEYS: tuple[str, ...] = _OT_JSONB_KEYS[:-1]

# Scalar Foundry-parity+ metadata mirrored on live + version rows.
_OT_META_KEYS: tuple[str, ...] = (
    "primary_key", "title_key", "plural_display_name", "lifecycle_status", "visibility", "icon",
    "deprecation_reason", "deprecation_deadline", "replacement_urn",
)


def title_of(instance: dict, object_type: dict | None = None) -> str:
    """Display title for an object instance — title_key, else primary_key, else id/name."""
    keys: list[str] = []
    if object_type:
        if object_type.get("title_key"):
            keys.append(object_type["title_key"])
        if object_type.get("primary_key"):
            keys.append(object_type["primary_key"])
    keys.extend(["name", "id"])
    mapping = (object_type or {}).get("property_mapping") or {}
    for key in keys:
        if key in instance and instance[key] is not None and instance[key] != "":
            return str(instance[key])
        col = mapping.get(key)
        if col and col in instance and instance[col] is not None and instance[col] != "":
            return str(instance[col])
    return str(instance.get("id") or "")


def validate_ot_metadata(
    *,
    property_mapping: dict,
    primary_key: str,
    title_key: str | None,
    lifecycle_status: str,
    visibility: str,
    deprecation_reason: str | None = None,
    deprecation_deadline=None,
    replacement_urn: str | None = None,
) -> dict:
    """Validate OT identity metadata. Returns normalized deprecation fields."""
    if visibility not in VALID_VISIBILITIES:
        raise ValueError(f"invalid visibility: {visibility!r} (must be one of {sorted(VALID_VISIBILITIES)})")
    if not primary_key:
        raise ValueError("primary_key is required")
    if primary_key not in property_mapping:
        raise ValueError(f"primary_key {primary_key!r} must be a key in property_mapping")
    if title_key and title_key not in property_mapping:
        raise ValueError(f"title_key {title_key!r} must be a key in property_mapping")
    return normalize_deprecation_metadata(
        lifecycle_status,
        deprecation_reason=deprecation_reason,
        deprecation_deadline=deprecation_deadline,
        replacement_urn=replacement_urn,
        target="object_type",
    )


def _parse_jsonb_keys(row: asyncpg.Record, keys: tuple[str, ...]) -> dict:
    """Deserialise the JSONB columns of an asyncpg record.

    asyncpg may return JSONB columns either as an unparsed string or already
    as a Python object depending on the driver/codec configuration — the
    isinstance guard handles both without failing on either. API callers
    always receive structured dicts/lists, never raw JSON strings.
    """
    result = dict(row)
    for key in keys:
        if isinstance(result.get(key), str):
            result[key] = json.loads(result[key])
    return result


async def _upsert_object_type(
    pool: asyncpg.Pool, urn: str, tenant_id: str, name: str, source_dataset_urn: str, mapping: dict, description: str
) -> None:
    """`description`:
    a mandatory natural-language description of what this ObjectType *is*,
    reviewed like code, refreshed from source truth on every startup — same
    treatment as `property_mapping`. Not excluded from the upsert the way
    `classification` deliberately is (that one is computed lineage, owned by
    `catalog.py`, never clobbered by a restart); a description is authored
    metadata, restart-safe to always refresh — *unless* `publish_object_type_version`
    has already moved this row past `version = 1` (a real governance
    change), in which case the boot-time reseed leaves it alone. See
    module docstring for why this guard exists.
    """
    await pool.execute(
        """
        INSERT INTO object_type (urn, tenant_id, name, source_dataset_urn, property_mapping, classification, description)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
        ON CONFLICT (urn) DO UPDATE SET
            source_dataset_urn = CASE WHEN object_type.version = 1 THEN EXCLUDED.source_dataset_urn ELSE object_type.source_dataset_urn END,
            property_mapping = CASE WHEN object_type.version = 1 THEN EXCLUDED.property_mapping ELSE object_type.property_mapping END,
            description = CASE WHEN object_type.version = 1 THEN EXCLUDED.description ELSE object_type.description END
        """,
        urn,
        tenant_id,
        name,
        source_dataset_urn,
        json.dumps(mapping),
        INITIAL_CLASSIFICATION,
        description,
    )


async def create_object_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    source_dataset_urn: str,
    property_mapping: dict,
    description: str,
    column_classification: Optional[dict[str, str]] = None,
    primary_key: str = "id",
    title_key: Optional[str] = None,
    plural_display_name: str = "",
    lifecycle_status: str = "experimental",
    visibility: str = "normal",
    icon: Optional[str] = None,
    deprecation_reason: Optional[str] = None,
    deprecation_deadline=None,
    replacement_urn: Optional[str] = None,
) -> dict:
    """Register a self-serve ObjectType mapped to a source dataset."""
    dep = validate_ot_metadata(
        property_mapping=property_mapping,
        primary_key=primary_key,
        title_key=title_key,
        lifecycle_status=lifecycle_status,
        visibility=visibility,
        deprecation_reason=deprecation_reason,
        deprecation_deadline=deprecation_deadline,
        replacement_urn=replacement_urn,
    )
    urn = object_type_urn(tenant_id, workspace_id, name)
    if await get_object_type(pool, urn) is not None:
        raise ValueError(f"an ObjectType named {name!r} already exists")
    await _upsert_object_type(pool, urn, tenant_id, name, source_dataset_urn, property_mapping, description)
    await pool.execute(
        """
        UPDATE object_type SET
            primary_key = $1, title_key = $2, plural_display_name = $3,
            lifecycle_status = $4, visibility = $5, icon = $6,
            deprecation_reason = $7, deprecation_deadline = $8, replacement_urn = $9
        WHERE urn = $10
        """,
        primary_key,
        title_key,
        plural_display_name or "",
        dep["lifecycle_status"],
        visibility,
        icon,
        dep["deprecation_reason"],
        dep["deprecation_deadline"],
        dep["replacement_urn"],
        urn,
    )

    declared = column_classification or {}
    if declared:
        overall = most_restrictive(*(Classification(value) for value in declared.values()))
        await pool.execute(
            "UPDATE object_type SET column_classification = $1::jsonb, classification = $2 WHERE urn = $3",
            json.dumps(declared), overall.value, urn,
        )
    definition_cache.invalidate_object_type(urn=urn, tenant_id=tenant_id)
    return await get_object_type(pool, urn)


async def get_object_type_by_dataset(pool: asyncpg.Pool, tenant_id: str, source_dataset_urn: str) -> dict | None:
    cache_key = definition_cache.object_type_dataset_key(tenant_id, source_dataset_urn)
    if definition_cache.has(cache_key):
        return definition_cache.get(cache_key)
    row = await pool.fetchrow(
        "SELECT * FROM object_type WHERE tenant_id = $1 AND source_dataset_urn = $2", tenant_id, source_dataset_urn
    )
    parsed = _parse_jsonb_keys(row, _OT_JSONB_KEYS) if row is not None else None
    if parsed is not None:
        definition_cache.put(cache_key, parsed)
    return parsed


async def get_object_type(pool: asyncpg.Pool, urn: str) -> dict | None:
    cache_key = definition_cache.object_type_key(urn)
    if definition_cache.has(cache_key):
        return definition_cache.get(cache_key)
    row = await pool.fetchrow("SELECT * FROM object_type WHERE urn = $1", urn)
    parsed = _parse_jsonb_keys(row, _OT_JSONB_KEYS) if row is not None else None
    if parsed is not None:
        definition_cache.put(cache_key, parsed)
    return parsed


def _parse_version_row(row: asyncpg.Record) -> dict:
    return _parse_jsonb_keys(row, _OTV_JSONB_KEYS)


async def list_object_type_versions(pool: asyncpg.Pool, object_type_urn: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM object_type_version WHERE object_type_urn = $1 ORDER BY version DESC", object_type_urn
    )
    return [_parse_version_row(row) for row in rows]


async def get_object_type_version(pool: asyncpg.Pool, object_type_urn: str, version: int) -> Optional[dict]:
    row = await pool.fetchrow(
        "SELECT * FROM object_type_version WHERE object_type_urn = $1 AND version = $2", object_type_urn, version
    )
    return _parse_version_row(row) if row else None


async def upsert_property_classification(
    conn: asyncpg.Connection, object_type_urn: str, property_name: str, classification: str
) -> None:
    await conn.execute(
        """
        INSERT INTO object_type_property (object_type_urn, property_name, classification)
        VALUES ($1, $2, $3)
        ON CONFLICT (object_type_urn, property_name) DO UPDATE SET classification = EXCLUDED.classification
        """,
        object_type_urn, property_name, classification,
    )
    definition_cache.invalidate(definition_cache.property_classifications_key(object_type_urn))


async def get_property_classifications(pool: asyncpg.Pool, object_type_urn: str) -> dict[str, str]:
    cache_key = definition_cache.property_classifications_key(object_type_urn)
    if definition_cache.has(cache_key):
        return definition_cache.get(cache_key) or {}
    rows = await pool.fetch(
        "SELECT property_name, classification FROM object_type_property WHERE object_type_urn = $1", object_type_urn
    )
    parsed = {row["property_name"]: row["classification"] for row in rows}
    definition_cache.put(cache_key, parsed)
    return parsed


async def list_object_types(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    """List all ObjectTypes for a tenant."""
    cache_key = definition_cache.object_type_list_key(tenant_id)
    if definition_cache.has(cache_key):
        return definition_cache.get(cache_key) or []
    rows = await pool.fetch("SELECT * FROM object_type WHERE tenant_id = $1 ORDER BY name", tenant_id)
    parsed = [_parse_jsonb_keys(row, _OT_JSONB_KEYS) for row in rows]
    definition_cache.put(cache_key, parsed)
    return parsed


async def delete_object_type(pool: asyncpg.Pool, urn: str) -> None:
    """Delete an ObjectType, verifying lifecycle state and dependencies."""
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT lifecycle_status FROM object_type WHERE urn = $1 FOR UPDATE", urn
        )
        if row is None:
            raise ValueError(f"unknown ObjectType: {urn}")
        if (row["lifecycle_status"] or "experimental") in NON_DELETABLE_OBJECT_TYPE_STATUSES:
            # Brand-new self-serve creates (no versions yet) may still be
            # rolled back after a failed SpiceDB seed — Foundry's active/
            # promoted delete ban applies once the type has entered versioning.
            version_count = await conn.fetchval(
                "SELECT COUNT(*) FROM object_type_version WHERE object_type_urn = $1", urn
            )
            if version_count:
                raise ValueError(
                    "cannot delete an active or promoted ObjectType — set lifecycle_status to deprecated "
                    "(or experimental/example) first"
                )
        version_count = await conn.fetchval(
            "SELECT COUNT(*) FROM object_type_version WHERE object_type_urn = $1", urn
        )
        if version_count:
            raise ValueError(
                f"refusing to delete {urn}: {version_count} object_type_version row(s) already exist"
            )
        branch_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ontology_branch WHERE object_type_urn = $1", urn
        )
        if branch_count:
            raise ValueError(f"refusing to delete {urn}: {branch_count} ontology_branch row(s) already exist")
        await conn.execute("DELETE FROM object_type_property WHERE object_type_urn = $1", urn)
        await conn.execute("DELETE FROM instance_marking WHERE object_type_urn = $1", urn)
        result = await conn.execute("DELETE FROM object_type WHERE urn = $1", urn)
        if result == "DELETE 0":
            raise ValueError(f"unknown ObjectType: {urn}")
    definition_cache.invalidate_object_type(urn=urn)
