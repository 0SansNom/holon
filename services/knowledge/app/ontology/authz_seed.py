"""SpiceDB bootstrap for the ontology's own resources — Knowledge owns
ObjectType (and Shared Property Types / RelationTypes), so it links its own
resources under the workspace itself; Identity only owns the tenant/workspace
side of the graph.
"""

from __future__ import annotations

from typing import Optional

import asyncpg
from holon_common.authz import PermissionClient

from .shared_property_types import list_shared_property_types, shared_property_type_urn
from .relation_types import list_relation_types
from .value_types import list_value_types, value_type_urn
from .urns import workspace_urn


async def ensure_authz_seeded(
    client: PermissionClient,
    schema_path: str,
    tenant_id: str,
    workspace_id: str,
    pool: Optional[asyncpg.Pool] = None,
) -> None:
    """`write_schema` is idempotent: calling it again with the same file
    is a no-op, and removes any dependency on Identity having started
    first (see `PermissionClient` docstring in `identity/app/main.py`).
    """
    from pathlib import Path

    await client.write_schema(Path(schema_path).read_text())
    w_urn = workspace_urn(tenant_id, workspace_id)
    # Backfill parent_workspace for every SPT so update/delete ReBAC checks
    # work for rows created before shared_property_type entered the schema.
    if pool is not None:
        for spt in await list_shared_property_types(pool, tenant_id):
            urn = spt.get("urn") or shared_property_type_urn(tenant_id, spt["api_name"])
            await client.write_relationship(
                resource_type="shared_property_type",
                resource_urn=urn,
                relation="parent_workspace",
                subject_type="workspace",
                subject_urn=w_urn,
            )
            project_urn = spt.get("project_urn")
            if project_urn:
                await client.write_relationship(
                    resource_type="shared_property_type",
                    resource_urn=urn,
                    relation="parent_project",
                    subject_type="project",
                    subject_urn=project_urn,
                )
        for relation in await list_relation_types(pool, tenant_id):
            urn = relation["urn"]
            await client.write_relationship(
                resource_type="relation_type",
                resource_urn=urn,
                relation="parent_workspace",
                subject_type="workspace",
                subject_urn=w_urn,
            )
            project_urn = relation.get("project_urn")
            if project_urn:
                await client.write_relationship(
                    resource_type="relation_type",
                    resource_urn=urn,
                    relation="parent_project",
                    subject_type="project",
                    subject_urn=project_urn,
                )
        for value_type in await list_value_types(pool, tenant_id):
            urn = value_type.get("urn") or value_type_urn(tenant_id, value_type["name"])
            await client.write_relationship(
                resource_type="value_type",
                resource_urn=urn,
                relation="parent_workspace",
                subject_type="workspace",
                subject_urn=w_urn,
            )
            project_urn = value_type.get("project_urn")
            if project_urn:
                await client.write_relationship(
                    resource_type="value_type",
                    resource_urn=urn,
                    relation="parent_project",
                    subject_type="project",
                    subject_urn=project_urn,
                )
