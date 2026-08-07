"""Branching + review: the same human-in-the-loop shape Actions already
use (a `write`-tier request, an `approve`-tier decision — role
separation, not a same-URN check, same as `action_approval`), applied to
ontology changes instead of data writes. A branch is a named pointer at
a specific `object_type_version` row; review approval merges by calling
`publishing.publish_object_type_version` unchanged, so every validation/
event it already does still applies.
"""

from __future__ import annotations

from typing import Optional

import asyncpg

from .publishing import propose_object_type_version, publish_object_type_version


def _parse_branch_row(row: asyncpg.Record) -> dict:
    return dict(row)


async def get_branch(pool: asyncpg.Pool, object_type_urn: str, branch_name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        "SELECT * FROM ontology_branch WHERE object_type_urn = $1 AND branch_name = $2", object_type_urn, branch_name
    )
    return _parse_branch_row(row) if row else None


async def list_branches(pool: asyncpg.Pool, object_type_urn: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM ontology_branch WHERE object_type_urn = $1 ORDER BY created_at DESC", object_type_urn
    )
    return [_parse_branch_row(row) for row in rows]


async def create_branch(
    pool: asyncpg.Pool,
    *,
    object_type_urn: str,
    branch_name: str,
    created_by_urn: str,
    property_mapping: Optional[dict] = None,
    description: Optional[str] = None,
    implements: Optional[list[str]] = None,
    derived_properties: Optional[dict[str, str]] = None,
    project_urn: Optional[str] = None,
    markings: Optional[list[str]] = None,
) -> dict:
    """Wraps `propose_object_type_version` with a human-readable name and
    an owner. The review gate below relies on role separation (branch
    creation only needs workspace `write`/editor; review needs `approve`
    /admin — enforced by `routers/ontology_admin.py`'s two different
    authorize calls, not a same-URN check here) — the exact same shape
    Actions already use for `action_approval`, just applied to ontology
    changes.
    """
    if await get_branch(pool, object_type_urn, branch_name) is not None:
        raise ValueError(f"branch already exists: {branch_name!r}")
    draft = await propose_object_type_version(
        pool,
        object_type_urn=object_type_urn,
        property_mapping=property_mapping,
        description=description,
        implements=implements,
        derived_properties=derived_properties,
        project_urn=project_urn,
        markings=markings,
    )
    await pool.execute(
        """
        INSERT INTO ontology_branch (object_type_urn, tenant_id, branch_name, version, created_by_urn, status)
        VALUES ($1, $2, $3, $4, $5, 'open')
        """,
        object_type_urn, draft["tenant_id"], branch_name, draft["version"], created_by_urn,
    )
    return await get_branch(pool, object_type_urn, branch_name)


async def update_branch_draft(
    pool: asyncpg.Pool,
    *,
    object_type_urn: str,
    branch_name: str,
    property_mapping: Optional[dict] = None,
    description: Optional[str] = None,
    implements: Optional[list[str]] = None,
    derived_properties: Optional[dict[str, str]] = None,
    project_urn: Optional[str] = None,
    markings: Optional[list[str]] = None,
) -> dict:
    """After a review requests changes, the branch gets a *new* draft
    version and its pointer moves forward — the same "append-only
    history, live pointer moves" shape `publish_object_type_version`
    already uses for `object_type` itself, one level up.
    """
    branch = await get_branch(pool, object_type_urn, branch_name)
    if branch is None:
        raise ValueError(f"unknown branch: {branch_name!r}")
    if branch["status"] != "open":
        raise ValueError(f"branch {branch_name!r} is {branch['status']}, not open")
    draft = await propose_object_type_version(
        pool,
        object_type_urn=object_type_urn,
        property_mapping=property_mapping,
        description=description,
        implements=implements,
        derived_properties=derived_properties,
        project_urn=project_urn,
        markings=markings,
    )
    await pool.execute(
        "UPDATE ontology_branch SET version = $1 WHERE id = $2", draft["version"], branch["id"],
    )
    return await get_branch(pool, object_type_urn, branch_name)


async def list_branch_reviews(pool: asyncpg.Pool, branch_id: int) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM ontology_review WHERE branch_id = $1 ORDER BY decided_at", branch_id)
    return [dict(row) for row in rows]


async def review_branch(
    pool: asyncpg.Pool,
    *,
    object_type_urn: str,
    branch_name: str,
    reviewer_urn: str,
    decision: str,
    note: Optional[str] = None,
    identity_url: Optional[str] = None,
    identity_token: Optional[str] = None,
) -> dict:
    """The merge gate. `decision == "approved"` publishes the branch's
    current draft version through the *existing*, unmodified
    `publish_object_type_version` — same `implements`/`derived_properties`
    /`project_urn` validation, same `knowledge.objecttype.published`
    event — and marks the branch `merged`. `"changes_requested"` just
    records the review and leaves the branch `open` for a follow-up
    `update_branch_draft`.
    """
    if decision not in ("approved", "changes_requested"):
        raise ValueError(f"invalid decision: {decision!r} (must be 'approved' or 'changes_requested')")
    branch = await get_branch(pool, object_type_urn, branch_name)
    if branch is None:
        raise ValueError(f"unknown branch: {branch_name!r}")
    if branch["status"] != "open":
        raise ValueError(f"branch {branch_name!r} is {branch['status']}, not open")

    await pool.execute(
        "INSERT INTO ontology_review (branch_id, tenant_id, reviewer_urn, decision, note) VALUES ($1, $2, $3, $4, $5)",
        branch["id"], branch["tenant_id"], reviewer_urn, decision, note,
    )
    if decision == "approved":
        await publish_object_type_version(
            pool,
            object_type_urn=object_type_urn,
            version=branch["version"],
            identity_url=identity_url,
            identity_token=identity_token,
        )
        await pool.execute("UPDATE ontology_branch SET status = 'merged' WHERE id = $1", branch["id"])
    return await get_branch(pool, object_type_urn, branch_name)
