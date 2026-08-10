"""Generic branch/review for the 4 governed registries that aren't
ObjectType (RelationType, ValueType, SharedPropertyType, ActionType —
matching Foundry's own branching scope minus Type groups/Rule sets).

Deliberately kept separate from `branching.py`, whose functions are
tightly coupled to `propose_object_type_version`/`publish_object_type_version`
and stay untouched. A branch here just holds a freeform
`proposed_definition` dict until review — no structural validation at
propose/draft time, same "define now, validate against real state at
the point of use" posture `action_types.py`'s own docstring already
states as this codebase's convention. Real validation happens at merge
time, by calling straight through to the real registry's `create_*`
(if the resource doesn't exist yet) or `update_*` (if it does) — so
every existing structural check those functions already do still
applies, unchanged.
"""

from __future__ import annotations

import json
from typing import Optional

import asyncpg

from . import interfaces as interfaces_module
from . import relation_types as relation_types_module
from . import shared_property_types as shared_property_types_module
from . import action_types as action_types_module
from . import value_types as value_types_module
from .urns import relation_type_urn

ALLOWED_RESOURCE_TYPES = {"interface_type", "relation_type", "value_type", "shared_property_type", "action_type"}


async def _get_resource(
    pool: asyncpg.Pool, *, resource_type: str, resource_name: str, tenant_id: str, workspace_id: str
) -> Optional[dict]:
    if resource_type == "interface_type":
        return await interfaces_module.get_interface_type(pool, tenant_id, resource_name)
    if resource_type == "relation_type":
        urn = relation_type_urn(tenant_id, workspace_id, resource_name)
        return await relation_types_module.get_relation_type(pool, urn)
    if resource_type == "value_type":
        return await value_types_module.get_value_type(pool, tenant_id, resource_name)
    if resource_type == "shared_property_type":
        return await shared_property_types_module.get_shared_property_type(pool, tenant_id, resource_name)
    if resource_type == "action_type":
        return await action_types_module.get_action_type(pool, tenant_id, resource_name)
    raise ValueError(f"unknown resource_type: {resource_type!r} (expected one of {sorted(ALLOWED_RESOURCE_TYPES)})")


async def _create_resource(
    pool: asyncpg.Pool, *, resource_type: str, resource_name: str, tenant_id: str, workspace_id: str, definition: dict
) -> dict:
    if resource_type == "interface_type":
        return await interfaces_module.create_interface_type(
            pool,
            tenant_id=tenant_id,
            name=resource_name,
            required_properties=definition.get("required_properties", []),
            required_actions=definition.get("required_actions", []),
            description=definition.get("description", ""),
        )
    if resource_type == "relation_type":
        return await relation_types_module.create_relation_type(
            pool,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name=resource_name,
            source_object_type=definition["source_object_type"],
            target_object_type=definition["target_object_type"],
            source_property=definition["source_property"],
            target_property=definition["target_property"],
            cardinality=definition["cardinality"],
        )
    if resource_type == "value_type":
        return await value_types_module.create_value_type(
            pool,
            tenant_id=tenant_id,
            name=resource_name,
            base_type=definition["base_type"],
            format_regex=definition.get("format_regex"),
            constraints=definition.get("constraints"),
            description=definition.get("description", ""),
        )
    if resource_type == "shared_property_type":
        return await shared_property_types_module.create_shared_property_type(
            pool,
            tenant_id=tenant_id,
            api_name=resource_name,
            display_name=definition["display_name"],
            value_type=definition["value_type"],
            description=definition.get("description", ""),
        )
    if resource_type == "action_type":
        return await action_types_module.create_action_type(
            pool,
            tenant_id=tenant_id,
            name=resource_name,
            target_object_type=definition.get("target_object_type"),
            target_interface=definition.get("target_interface"),
            required_permission=definition["required_permission"],
            risk_level=definition["risk_level"],
            description=definition.get("description", ""),
            parameters=definition.get("parameters", []),
            edits=definition.get("edits", []),
            submission_criteria=definition.get("submission_criteria"),
            function_side_effect=definition.get("function_side_effect"),
            writeback_dataset=definition.get("writeback_dataset"),
            edit_function=definition.get("edit_function"),
            sections=definition.get("sections"),
        )
    raise ValueError(f"unknown resource_type: {resource_type!r} (expected one of {sorted(ALLOWED_RESOURCE_TYPES)})")


async def _update_resource(
    pool: asyncpg.Pool, *, resource_type: str, resource_name: str, tenant_id: str, workspace_id: str, definition: dict
) -> dict:
    if resource_type == "interface_type":
        return await interfaces_module.update_interface_type(
            pool,
            tenant_id=tenant_id,
            name=resource_name,
            required_properties=definition.get("required_properties"),
            required_actions=definition.get("required_actions"),
            description=definition.get("description"),
        )
    if resource_type == "relation_type":
        return await relation_types_module.update_relation_type(
            pool,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name=resource_name,
            target_property=definition.get("target_property"),
            cardinality=definition.get("cardinality"),
        )
    if resource_type == "value_type":
        return await value_types_module.update_value_type(
            pool,
            tenant_id=tenant_id,
            name=resource_name,
            format_regex=definition.get("format_regex"),
            constraints=definition.get("constraints"),
            description=definition.get("description"),
        )
    if resource_type == "shared_property_type":
        return await shared_property_types_module.update_shared_property_type(
            pool,
            tenant_id=tenant_id,
            api_name=resource_name,
            display_name=definition.get("display_name"),
            description=definition.get("description"),
        )
    if resource_type == "action_type":
        # `create_action_type`'s SQL is already `ON CONFLICT DO UPDATE` —
        # merging a branch onto an existing Action Type just calls it
        # again, same as the `PUT /action-types/{name}` router endpoint does.
        return await _create_resource(
            pool,
            resource_type=resource_type,
            resource_name=resource_name,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            definition=definition,
        )
    raise ValueError(f"unknown resource_type: {resource_type!r} (expected one of {sorted(ALLOWED_RESOURCE_TYPES)})")


async def get_resource_branch(
    pool: asyncpg.Pool, *, resource_type: str, resource_name: str, branch_name: str, tenant_id: str
) -> Optional[dict]:
    row = await pool.fetchrow(
        """
        SELECT * FROM ontology_branch
        WHERE tenant_id = $1 AND resource_type = $2 AND resource_name = $3 AND branch_name = $4
        """,
        tenant_id, resource_type, resource_name, branch_name,
    )
    return dict(row) if row else None


async def list_resource_branches(pool: asyncpg.Pool, *, resource_type: str, resource_name: str, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT * FROM ontology_branch
        WHERE tenant_id = $1 AND resource_type = $2 AND resource_name = $3
        ORDER BY created_at DESC
        """,
        tenant_id, resource_type, resource_name,
    )
    return [dict(row) for row in rows]


async def create_resource_branch(
    pool: asyncpg.Pool,
    *,
    resource_type: str,
    resource_name: str,
    branch_name: str,
    created_by_urn: str,
    tenant_id: str,
    proposed_definition: dict,
) -> dict:
    if resource_type not in ALLOWED_RESOURCE_TYPES:
        raise ValueError(f"unknown resource_type: {resource_type!r} (expected one of {sorted(ALLOWED_RESOURCE_TYPES)})")
    if await get_resource_branch(pool, resource_type=resource_type, resource_name=resource_name, branch_name=branch_name, tenant_id=tenant_id) is not None:
        raise ValueError(f"branch already exists: {branch_name!r}")
    await pool.execute(
        """
        INSERT INTO ontology_branch
            (resource_type, resource_name, tenant_id, branch_name, created_by_urn, status, proposed_definition)
        VALUES ($1, $2, $3, $4, $5, 'open', $6::jsonb)
        """,
        resource_type, resource_name, tenant_id, branch_name, created_by_urn, json.dumps(proposed_definition),
    )
    return await get_resource_branch(pool, resource_type=resource_type, resource_name=resource_name, branch_name=branch_name, tenant_id=tenant_id)


async def update_resource_branch_draft(
    pool: asyncpg.Pool,
    *,
    resource_type: str,
    resource_name: str,
    branch_name: str,
    tenant_id: str,
    proposed_definition: dict,
) -> dict:
    branch = await get_resource_branch(pool, resource_type=resource_type, resource_name=resource_name, branch_name=branch_name, tenant_id=tenant_id)
    if branch is None:
        raise ValueError(f"unknown branch: {branch_name!r}")
    if branch["status"] != "open":
        raise ValueError(f"branch {branch_name!r} is {branch['status']}, not open")

    await pool.execute(
        "UPDATE ontology_branch SET proposed_definition = $1::jsonb WHERE id = $2",
        json.dumps(proposed_definition), branch["id"],
    )
    return await get_resource_branch(pool, resource_type=resource_type, resource_name=resource_name, branch_name=branch_name, tenant_id=tenant_id)


async def list_resource_branch_reviews(pool: asyncpg.Pool, branch_id: int) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM ontology_review WHERE branch_id = $1 ORDER BY decided_at", branch_id)
    return [dict(row) for row in rows]


async def review_resource_branch(
    pool: asyncpg.Pool,
    *,
    resource_type: str,
    resource_name: str,
    branch_name: str,
    reviewer_urn: str,
    decision: str,
    note: Optional[str] = None,
    tenant_id: str,
    workspace_id: str,
) -> dict:
    """The merge gate. `decision == "approved"` calls through to the real
    registry's `create_*` (resource doesn't exist yet) or `update_*`
    (it does) — every existing structural validation those functions
    already do still applies, unchanged. `"changes_requested"` just
    records the review and leaves the branch `open`.
    """
    if decision not in ("approved", "changes_requested"):
        raise ValueError(f"invalid decision: {decision!r} (must be 'approved' or 'changes_requested')")
    branch = await get_resource_branch(pool, resource_type=resource_type, resource_name=resource_name, branch_name=branch_name, tenant_id=tenant_id)
    if branch is None:
        raise ValueError(f"unknown branch: {branch_name!r}")
    if branch["status"] != "open":
        raise ValueError(f"branch {branch_name!r} is {branch['status']}, not open")

    await pool.execute(
        "INSERT INTO ontology_review (branch_id, tenant_id, reviewer_urn, decision, note) VALUES ($1, $2, $3, $4, $5)",
        branch["id"], tenant_id, reviewer_urn, decision, note,
    )
    if decision == "approved":
        definition = branch["proposed_definition"]
        if isinstance(definition, str):
            definition = json.loads(definition)
        existing = await _get_resource(pool, resource_type=resource_type, resource_name=resource_name, tenant_id=tenant_id, workspace_id=workspace_id)
        if existing is None:
            await _create_resource(
                pool, resource_type=resource_type, resource_name=resource_name, tenant_id=tenant_id,
                workspace_id=workspace_id, definition=definition,
            )
        else:
            await _update_resource(
                pool, resource_type=resource_type, resource_name=resource_name, tenant_id=tenant_id,
                workspace_id=workspace_id, definition=definition,
            )
        await pool.execute("UPDATE ontology_branch SET status = 'merged' WHERE id = $1", branch["id"])
    return await get_resource_branch(pool, resource_type=resource_type, resource_name=resource_name, branch_name=branch_name, tenant_id=tenant_id)
