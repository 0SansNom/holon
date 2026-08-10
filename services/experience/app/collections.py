"""Collections — a named, curated, cross-project grouping of resources.

Deliberately independent of both `resource_tag` (a resource's own
attributes, not a container) and `project_urn` (a single-valued
governance/organization scope): a Collection is many-to-many and can mix
resources from different projects or none at all, closer to a playlist
than a folder. Authorization is workspace `write` throughout — same bar
Applications already use — since a Collection isn't owned by any one
project or resource for a narrower permission to attach to.
"""

from __future__ import annotations

from typing import Optional

import asyncpg

DDL = """
CREATE TABLE IF NOT EXISTS collection (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS collection_member (
    collection_id BIGINT NOT NULL REFERENCES collection(id) ON DELETE CASCADE,
    resource_urn TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    added_by_urn TEXT NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (collection_id, resource_urn)
);
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


async def create_collection(
    pool: asyncpg.Pool, *, tenant_id: str, name: str, description: str, created_by_urn: str
) -> dict:
    await pool.execute(
        "INSERT INTO collection (tenant_id, name, description, created_by_urn) VALUES ($1, $2, $3, $4)",
        tenant_id, name, description, created_by_urn,
    )
    return await get_collection_by_name(pool, tenant_id=tenant_id, name=name)


async def get_collection_by_name(pool: asyncpg.Pool, *, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM collection WHERE tenant_id = $1 AND name = $2", tenant_id, name)
    return dict(row) if row else None


async def get_collection(pool: asyncpg.Pool, *, tenant_id: str, collection_id: int) -> Optional[dict]:
    row = await pool.fetchrow(
        "SELECT * FROM collection WHERE tenant_id = $1 AND id = $2", tenant_id, collection_id,
    )
    return dict(row) if row else None


async def list_collections(pool: asyncpg.Pool, *, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM collection WHERE tenant_id = $1 ORDER BY name", tenant_id)
    return [dict(row) for row in rows]


async def delete_collection(pool: asyncpg.Pool, *, tenant_id: str, collection_id: int) -> None:
    await pool.execute("DELETE FROM collection WHERE tenant_id = $1 AND id = $2", tenant_id, collection_id)


async def list_members(pool: asyncpg.Pool, *, tenant_id: str, collection_id: int) -> list[str]:
    rows = await pool.fetch(
        "SELECT resource_urn FROM collection_member WHERE tenant_id = $1 AND collection_id = $2 ORDER BY added_at",
        tenant_id, collection_id,
    )
    return [row["resource_urn"] for row in rows]


async def set_members(
    pool: asyncpg.Pool, *, tenant_id: str, collection_id: int, resource_urns: list[str], added_by_urn: str
) -> list[str]:
    """Full-replace, same "set the whole list" shape `resource_tags.set_tags`
    already uses — simpler for the frontend's multi-select "which
    collections is this resource in" dialog than per-member add/remove
    calls.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM collection_member WHERE tenant_id = $1 AND collection_id = $2", tenant_id, collection_id,
            )
            for resource_urn in resource_urns:
                await conn.execute(
                    """
                    INSERT INTO collection_member (collection_id, resource_urn, tenant_id, added_by_urn)
                    VALUES ($1, $2, $3, $4)
                    """,
                    collection_id, resource_urn, tenant_id, added_by_urn,
                )
    return await list_members(pool, tenant_id=tenant_id, collection_id=collection_id)


async def add_member(pool: asyncpg.Pool, *, tenant_id: str, collection_id: int, resource_urn: str, added_by_urn: str) -> None:
    """Single-member add/remove — the counterpart `set_members`' "replace
    the whole list" shape doesn't fit `ResourceActionsMenu`'s "Add to
    collection…" checkbox toggle well (that call site knows only the one
    resource being toggled, not every other collection's full membership).
    """
    await pool.execute(
        """
        INSERT INTO collection_member (collection_id, resource_urn, tenant_id, added_by_urn)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (collection_id, resource_urn) DO NOTHING
        """,
        collection_id, resource_urn, tenant_id, added_by_urn,
    )


async def remove_member(pool: asyncpg.Pool, *, tenant_id: str, collection_id: int, resource_urn: str) -> None:
    await pool.execute(
        "DELETE FROM collection_member WHERE tenant_id = $1 AND collection_id = $2 AND resource_urn = $3",
        tenant_id, collection_id, resource_urn,
    )


async def list_collections_for_resource(pool: asyncpg.Pool, *, tenant_id: str, resource_urn: str) -> list[dict]:
    """The other direction of the many-to-many — which collections
    already contain this URN, for `ResourceActionsMenu`'s "Add to
    collection…" dialog to pre-check the right boxes.
    """
    rows = await pool.fetch(
        """
        SELECT c.* FROM collection c
        JOIN collection_member m ON m.collection_id = c.id
        WHERE c.tenant_id = $1 AND m.resource_urn = $2
        ORDER BY c.name
        """,
        tenant_id, resource_urn,
    )
    return [dict(row) for row in rows]
