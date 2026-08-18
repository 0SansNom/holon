"""Branching and review — governance workflow for ontology changes."""

from __future__ import annotations

from typing import Optional

import asyncpg

from .object_types import get_object_type, get_object_type_version
from .publishing import (
    _invalidate_published_cache,
    _run_publish_validations,
    _write_publish,
    propose_object_type_version,
    publish_object_type_version,
)



async def get_branch(pool: asyncpg.Pool, object_type_urn: str, branch_name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        "SELECT * FROM ontology_branch WHERE object_type_urn = $1 AND branch_name = $2", object_type_urn, branch_name
    )
    return dict(row) if row else None


async def list_branches(pool: asyncpg.Pool, object_type_urn: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM ontology_branch WHERE object_type_urn = $1 ORDER BY created_at DESC", object_type_urn
    )
    return [dict(row) for row in rows]


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
    property_formats: Optional[dict[str, dict]] = None,
    conditional_formats: Optional[dict[str, list]] = None,
    property_types: Optional[dict[str, dict]] = None,
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
        property_formats=property_formats,
        conditional_formats=conditional_formats,
        property_types=property_types,
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
    property_formats: Optional[dict[str, dict]] = None,
    conditional_formats: Optional[dict[str, list]] = None,
    property_types: Optional[dict[str, dict]] = None,
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
        property_formats=property_formats,
        conditional_formats=conditional_formats,
        property_types=property_types,
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
    current draft version and marks the branch `merged` **in the same
    transaction** — eliminating the previous split where a crash between
    `publish_object_type_version` and `UPDATE status = 'merged'` would
    leave the branch `open` despite the publish having already succeeded.
    Validations still run outside the transaction (some make HTTP calls).
    `"changes_requested"` just records the review and leaves the branch
    `open` for a follow-up `update_branch_draft`.
    """
    if decision not in ("approved", "changes_requested"):
        raise ValueError(f"invalid decision: {decision!r} (must be 'approved' or 'changes_requested')")
    branch = await get_branch(pool, object_type_urn, branch_name)
    if branch is None:
        raise ValueError(f"unknown branch: {branch_name!r}")
    if branch["status"] != "open":
        raise ValueError(f"branch {branch_name!r} is {branch['status']}, not open")

    if decision == "approved":
        draft_version = branch["version"]
        draft = await get_object_type_version(pool, object_type_urn, draft_version)
        if draft is None:
            raise ValueError(f"no version {draft_version} found for {object_type_urn}")
        if draft["status"] == "published":
            raise ValueError(f"version {draft_version} of {object_type_urn} is already published")

        current = await get_object_type(pool, object_type_urn)
        previous_version = current["version"] if current else None
        if previous_version is not None and draft_version <= previous_version:
            raise ValueError(
                f"cannot publish version {draft_version} of {object_type_urn}: "
                f"live is already at version {previous_version}"
            )
        object_type_name = current["name"] if current else object_type_urn.rsplit(":", 1)[-1]
        implements = draft.get("implements") or []
        derived_properties = draft.get("derived_properties") or {}
        project_urn = draft.get("project_urn")
        markings = draft.get("markings") or []
        property_formats = draft.get("property_formats") or {}
        conditional_formats = draft.get("conditional_formats") or {}
        property_types = draft.get("property_types") or {}
        link_constraint_bindings = draft.get("link_constraint_bindings") or {}
        interface_property_bindings = draft.get("interface_property_bindings") or {}

        # Run validations outside the transaction — some make HTTP calls
        # (`_validate_project_scope` via httpx) and holding a DB connection
        # open for a remote request would waste a pool slot unnecessarily.
        await _run_publish_validations(
            pool,
            draft=draft,
            current=current,
            object_type_name=object_type_name,
            implements=implements,
            derived_properties=derived_properties,
            project_urn=project_urn,
            markings=markings,
            property_formats=property_formats,
            conditional_formats=conditional_formats,
            property_types=property_types,
            link_constraint_bindings=link_constraint_bindings,
            interface_property_bindings=interface_property_bindings,
            identity_url=identity_url,
            identity_token=identity_token,
        )

        # Atomic: review record + publish + branch merge in one transaction.
        # Previously split across two transactions; a crash between them left
        # the branch `open` despite the publish having already succeeded.
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO ontology_review (branch_id, tenant_id, reviewer_urn, decision, note) VALUES ($1, $2, $3, $4, $5)",
                branch["id"], branch["tenant_id"], reviewer_urn, decision, note,
            )
            await _write_publish(
                conn,
                object_type_urn=object_type_urn,
                version=draft_version,
                draft=draft,
                current=current,
                previous_version=previous_version,
                implements=implements,
                derived_properties=derived_properties,
                project_urn=project_urn,
                markings=markings,
                property_formats=property_formats,
                conditional_formats=conditional_formats,
                property_types=property_types,
            )
            await conn.execute("UPDATE ontology_branch SET status = 'merged' WHERE id = $1", branch["id"])
        _invalidate_published_cache(object_type_urn, current=current, draft=draft)
    else:
        await pool.execute(
            "INSERT INTO ontology_review (branch_id, tenant_id, reviewer_urn, decision, note) VALUES ($1, $2, $3, $4, $5)",
            branch["id"], branch["tenant_id"], reviewer_urn, decision, note,
        )

    return await get_branch(pool, object_type_urn, branch_name)
