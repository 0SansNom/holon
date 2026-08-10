"""Project pins — Foundry's Projects-page "Pinned" section equivalent.

Deliberately project-scoped, not global like `resource_tag`'s
tags/featured: the same resource can be pinned in one project's view and
not another's (or not be in any project at all and simply not pinnable).
Authorization is the *project's* own `write` (`main.py`'s
`pin_resource`/`unpin_resource`) — curating what shows up pinned in a
project is a project-management action, the same tier that already
governs everything else about that project, not a property of the
pinned resource itself.
"""

from __future__ import annotations

import asyncpg

DDL = """
CREATE TABLE IF NOT EXISTS project_pin (
    project_urn TEXT NOT NULL,
    resource_urn TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    pinned_by_urn TEXT NOT NULL,
    pinned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_urn, resource_urn)
);
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


async def pin(pool: asyncpg.Pool, *, tenant_id: str, project_urn: str, resource_urn: str, pinned_by_urn: str) -> None:
    await pool.execute(
        """
        INSERT INTO project_pin (project_urn, resource_urn, tenant_id, pinned_by_urn)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (project_urn, resource_urn) DO NOTHING
        """,
        project_urn, resource_urn, tenant_id, pinned_by_urn,
    )


async def unpin(pool: asyncpg.Pool, *, tenant_id: str, project_urn: str, resource_urn: str) -> None:
    await pool.execute(
        "DELETE FROM project_pin WHERE tenant_id = $1 AND project_urn = $2 AND resource_urn = $3",
        tenant_id, project_urn, resource_urn,
    )


async def list_pins(pool: asyncpg.Pool, *, tenant_id: str, project_urn: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM project_pin WHERE tenant_id = $1 AND project_urn = $2 ORDER BY pinned_at", tenant_id, project_urn,
    )
    return [dict(row) for row in rows]
