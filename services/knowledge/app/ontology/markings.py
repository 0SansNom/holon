"""Markings: a genuinely separate mechanism from
`classification`, not a replacement — named, admin-created labels (e.g.
"PII", "Export-Controlled") that compose (a resource can carry several;
a principal must hold every one, checked via the SpiceDB `marking`
resource's `hold` permission, `core.py`'s `_authorize_markings`).
ObjectType-wide markings validation at publish time
(`_validate_markings`, called from `publishing.py`) lives here since it's
purely a registry lookup against this module's own `marking` table, not
a cross-module concern; `instance_marking` is the separate, unversioned
per-instance case — a marking set directly on one object (e.g. this one
Customer row, not every Customer), enforced at the same read choke point
(`core._resolve_one`/`core._resolve_many`) rather than a second one.
"""

from __future__ import annotations

import json
from typing import Optional

import asyncpg


async def create_marking(pool: asyncpg.Pool, *, tenant_id: str, name: str, description: str = "") -> dict:
    await pool.execute(
        "INSERT INTO marking (tenant_id, name, description) VALUES ($1, $2, $3)", tenant_id, name, description,
    )
    return await get_marking(pool, tenant_id, name)


async def get_marking(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM marking WHERE tenant_id = $1 AND name = $2", tenant_id, name)
    return dict(row) if row else None


async def list_markings(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM marking WHERE tenant_id = $1 ORDER BY name", tenant_id)
    return [dict(row) for row in rows]


async def _validate_markings(pool: asyncpg.Pool, *, tenant_id: str, markings: list[str]) -> None:
    """Enforced at publish time (called from `publishing.py`), same tier
    as `_validate_implements`/`_validate_derived_properties`: a marking
    must name a real, registered `marking` row — declaring a resource
    "PII" is a checked reference to an admin-created label, not an
    arbitrary string anyone can invent on the fly (that would make the
    grant side, `_authorize_markings` checking SpiceDB
    `marking:{name}#hold`, meaningless — a principal could never hold a
    marking that was never registered for anyone to be granted).
    """
    for name in markings:
        if await get_marking(pool, tenant_id, name) is None:
            raise ValueError(f"unknown marking: {name!r}")


async def set_instance_markings(
    pool: asyncpg.Pool, *, object_type_urn: str, tenant_id: str, instance_id: str, markings: list[str]
) -> list[str]:
    """The other attachment point the plan calls out alongside ObjectType-
    wide markings: labeling one specific instance (e.g. this Customer,
    not every Customer) — deliberately unversioned (unlike `implements`/
    `derived_properties`/ObjectType-wide `markings`, there's no
    draft/publish/review workflow for a single row's label, same
    reasoning `upsert_property_classification` already applies).
    """
    await _validate_markings(pool, tenant_id=tenant_id, markings=markings)
    await pool.execute(
        """
        INSERT INTO instance_marking (object_type_urn, tenant_id, instance_id, markings, updated_at)
        VALUES ($1, $2, $3, $4::jsonb, now())
        ON CONFLICT (object_type_urn, tenant_id, instance_id) DO UPDATE SET
            markings = EXCLUDED.markings, updated_at = now()
        """,
        object_type_urn, tenant_id, instance_id, json.dumps(markings),
    )
    return markings


async def get_instance_markings_bulk(
    pool: asyncpg.Pool, *, object_type_urn: str, tenant_id: str, instance_ids: list[str]
) -> dict[str, list[str]]:
    """Bulk lookup for `core._resolve_many`'s read choke point — one query
    for an entire result page rather than N+1, same discipline
    `get_property_classifications` already applies for property masking.
    """
    if not instance_ids:
        return {}
    rows = await pool.fetch(
        """
        SELECT instance_id, markings FROM instance_marking
        WHERE object_type_urn = $1 AND tenant_id = $2 AND instance_id = ANY($3::text[])
        """,
        object_type_urn, tenant_id, instance_ids,
    )
    result = {}
    for row in rows:
        markings = row["markings"]
        result[row["instance_id"]] = json.loads(markings) if isinstance(markings, str) else markings
    return result
