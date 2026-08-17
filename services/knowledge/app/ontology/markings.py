"""Markings: named clearance labels, separate from `classification`.

A resource can carry several markings. Enforcement is **per category**:

- ``CONJUNCTIVE`` (default): principal must hold **every** applied marking
  in that category.
- ``DISJUNCTIVE``: principal must hold **at least one** applied marking
  in that category.

Across categories the checks AND together. SpiceDB ``marking`` remains
flat (``hold = holder + admin``); names stay unique per tenant so OT /
instance JSONB lists and marking URNs keep working. Each marking also has
a stable UUID ``id`` and belongs to a ``marking_category``.
"""

from __future__ import annotations

import json
import uuid
from typing import Optional

import asyncpg

DEFAULT_CATEGORY_NAME = "Default"


def _serialize_category(row: asyncpg.Record | dict) -> dict:
    d = dict(row)
    d["id"] = str(d["id"])
    if d.get("created_at") is not None and hasattr(d["created_at"], "isoformat"):
        d["created_at"] = d["created_at"].isoformat()
    return d


def _serialize_marking(row: asyncpg.Record | dict) -> dict:
    d = dict(row)
    d["id"] = str(d["id"])
    d["category_id"] = str(d["category_id"])
    if d.get("created_at") is not None and hasattr(d["created_at"], "isoformat"):
        d["created_at"] = d["created_at"].isoformat()
    return d


async def create_marking_category(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    description: str = "",
    category_type: str = "CONJUNCTIVE",
    marking_type: str = "MANDATORY",
) -> dict:
    category_type = category_type.upper()
    marking_type = marking_type.upper()
    if category_type not in ("CONJUNCTIVE", "DISJUNCTIVE"):
        raise ValueError(f"category_type must be CONJUNCTIVE or DISJUNCTIVE, got {category_type!r}")
    if marking_type != "MANDATORY":
        raise ValueError(f"marking_type must be MANDATORY, got {marking_type!r}")
    cat_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO marking_category (id, tenant_id, name, description, category_type, marking_type)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        cat_id,
        tenant_id,
        name,
        description,
        category_type,
        marking_type,
    )
    return await get_marking_category(pool, tenant_id, str(cat_id))


async def get_marking_category(pool: asyncpg.Pool, tenant_id: str, category_ref: str) -> Optional[dict]:
    """Resolve by UUID id or by name within the tenant."""
    row = await pool.fetchrow(
        """
        SELECT * FROM marking_category
        WHERE tenant_id = $1 AND (id::text = $2 OR name = $2)
        """,
        tenant_id,
        category_ref,
    )
    return _serialize_category(row) if row else None


async def list_marking_categories(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM marking_category WHERE tenant_id = $1 ORDER BY name",
        tenant_id,
    )
    return [_serialize_category(r) for r in rows]


async def ensure_default_category(pool: asyncpg.Pool, tenant_id: str) -> dict:
    existing = await get_marking_category(pool, tenant_id, DEFAULT_CATEGORY_NAME)
    if existing is not None:
        return existing
    try:
        return await create_marking_category(
            pool,
            tenant_id=tenant_id,
            name=DEFAULT_CATEGORY_NAME,
            description="Default mandatory marking category",
            category_type="CONJUNCTIVE",
            marking_type="MANDATORY",
        )
    except asyncpg.UniqueViolationError:
        found = await get_marking_category(pool, tenant_id, DEFAULT_CATEGORY_NAME)
        assert found is not None
        return found


async def create_marking(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    description: str = "",
    category_id: Optional[str] = None,
) -> dict:
    if category_id is None:
        category = await ensure_default_category(pool, tenant_id)
        category_id = category["id"]
    else:
        category = await get_marking_category(pool, tenant_id, category_id)
        if category is None:
            raise ValueError(f"unknown marking category: {category_id}")
        category_id = category["id"]

    marking_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO marking (id, tenant_id, name, description, category_id)
        VALUES ($1, $2, $3, $4, $5::uuid)
        """,
        marking_id,
        tenant_id,
        name,
        description,
        category_id,
    )
    return await get_marking(pool, tenant_id, name)


async def get_marking(pool: asyncpg.Pool, tenant_id: str, marking_ref: str) -> Optional[dict]:
    """Resolve by UUID id or by unique name within the tenant."""
    row = await pool.fetchrow(
        """
        SELECT m.*, c.name AS category_name, c.category_type, c.marking_type
        FROM marking m
        JOIN marking_category c ON c.id = m.category_id
        WHERE m.tenant_id = $1 AND (m.id::text = $2 OR m.name = $2)
        """,
        tenant_id,
        marking_ref,
    )
    if row is None:
        return None
    out = _serialize_marking(row)
    return out


async def list_markings(
    pool: asyncpg.Pool,
    tenant_id: str,
    *,
    category_id: Optional[str] = None,
) -> list[dict]:
    if category_id:
        rows = await pool.fetch(
            """
            SELECT m.*, c.name AS category_name, c.category_type, c.marking_type
            FROM marking m
            JOIN marking_category c ON c.id = m.category_id
            WHERE m.tenant_id = $1 AND m.category_id::text = $2
            ORDER BY m.name
            """,
            tenant_id,
            category_id,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT m.*, c.name AS category_name, c.category_type, c.marking_type
            FROM marking m
            JOIN marking_category c ON c.id = m.category_id
            WHERE m.tenant_id = $1
            ORDER BY m.name
            """,
            tenant_id,
        )
    return [_serialize_marking(r) for r in rows]


async def marking_authz_meta(
    pool: asyncpg.Pool, tenant_id: str, markings: list[str]
) -> list[dict]:
    """Category metadata for applied marking names (authz evaluation)."""
    if not markings:
        return []
    rows = await pool.fetch(
        """
        SELECT m.name, m.category_id, c.category_type
        FROM marking m
        JOIN marking_category c ON c.id = m.category_id
        WHERE m.tenant_id = $1 AND m.name = ANY($2::text[])
        """,
        tenant_id,
        markings,
    )
    return [
        {
            "name": r["name"],
            "category_id": str(r["category_id"]),
            "category_type": r["category_type"],
        }
        for r in rows
    ]


def category_groups_satisfied(
    meta: list[dict],
    held: dict[str, bool],
) -> bool:
    """Pure evaluator used by `_authorize_markings` and unit tests.

    ``held`` maps marking name → whether the principal holds it.
    """
    by_category: dict[str, tuple[str, list[str]]] = {}
    for item in meta:
        cat_id = item["category_id"]
        if cat_id not in by_category:
            by_category[cat_id] = (item["category_type"], [])
        by_category[cat_id][1].append(item["name"])

    for _cat_id, (category_type, names) in by_category.items():
        flags = [bool(held.get(name)) for name in names]
        if category_type == "DISJUNCTIVE":
            if not any(flags):
                return False
        else:
            if not all(flags):
                return False
    return True


async def _validate_markings(pool: asyncpg.Pool, *, tenant_id: str, markings: list[str]) -> None:
    """Enforced at publish time (called from `publishing.py`): every name
    must exist in the tenant marking registry.
    """
    if not markings:
        return
    rows = await pool.fetch(
        "SELECT name FROM marking WHERE tenant_id = $1 AND name = ANY($2::text[])",
        tenant_id,
        markings,
    )
    found = {row["name"] for row in rows}
    missing = [m for m in markings if m not in found]
    if missing:
        raise ValueError(f"unknown marking{'s' if len(missing) > 1 else ''}: {missing!r}")


async def set_instance_markings(
    pool: asyncpg.Pool, *, object_type_urn: str, tenant_id: str, instance_id: str, markings: list[str]
) -> list[str]:
    await _validate_markings(pool, tenant_id=tenant_id, markings=markings)
    await pool.execute(
        """
        INSERT INTO instance_marking (object_type_urn, tenant_id, instance_id, markings, updated_at)
        VALUES ($1, $2, $3, $4::jsonb, now())
        ON CONFLICT (object_type_urn, tenant_id, instance_id) DO UPDATE SET
            markings = EXCLUDED.markings, updated_at = now()
        """,
        object_type_urn,
        tenant_id,
        instance_id,
        json.dumps(markings),
    )
    return markings


async def get_instance_markings_bulk(
    pool: asyncpg.Pool, *, object_type_urn: str, tenant_id: str, instance_ids: list[str]
) -> dict[str, list[str]]:
    if not instance_ids:
        return {}
    rows = await pool.fetch(
        """
        SELECT instance_id, markings FROM instance_marking
        WHERE object_type_urn = $1 AND tenant_id = $2 AND instance_id = ANY($3::text[])
        """,
        object_type_urn,
        tenant_id,
        instance_ids,
    )
    result = {}
    for row in rows:
        markings = row["markings"]
        result[row["instance_id"]] = json.loads(markings) if isinstance(markings, str) else markings
    return result
