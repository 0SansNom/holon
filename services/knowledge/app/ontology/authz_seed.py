"""SpiceDB bootstrap for the ontology's own resources — Knowledge owns
ObjectType (and Shared Property Types / RelationTypes), so it links its own
resources under the workspace itself; Identity only owns the tenant/workspace
side of the graph, and is the sole writer of the SpiceDB schema.
"""

from __future__ import annotations

import logging
from typing import Optional

import asyncpg

from holon_common import parse_urn
from holon_common.authz import PermissionClient
from holon_common.urn import InvalidURNError

from .object_types import list_object_types
from .shared_property_types import list_shared_property_types, shared_property_type_urn
from .relation_types import list_relation_types
from .value_types import list_value_types, value_type_urn
from .urns import workspace_urn

logger = logging.getLogger("knowledge.authz_seed")

# Business workspace lives in the URN for object types and relation types.
# Shared property types and value types use the `global` namespace; they are
# parented once per tenant, onto that tenant's default business workspace.
_GLOBAL_NAMESPACE = "global"

_SCOPED_URNS_SQL = """
SELECT urn FROM object_type
UNION
SELECT urn FROM relation_type
"""

_TENANT_SQL = """
SELECT tenant_id FROM object_type
UNION SELECT tenant_id FROM relation_type
UNION SELECT tenant_id FROM shared_property_type
UNION SELECT tenant_id FROM value_type
"""


def _parsed_workspace(urn: str) -> Optional[tuple[str, str]]:
    try:
        parsed = parse_urn(urn)
    except InvalidURNError:
        return None
    return parsed.tenant, parsed.workspace


def _include_resource(urn: str, tenant_id: str, workspace_id: str, *, global_parent: str) -> bool:
    parsed = _parsed_workspace(urn)
    if parsed is None:
        return True
    resource_tenant, resource_workspace = parsed
    if resource_tenant != tenant_id:
        return False
    if resource_workspace == _GLOBAL_NAMESPACE:
        return workspace_id == global_parent
    return resource_workspace == workspace_id


def _parent_workspace_id(urn: str, workspace_id: str) -> str:
    parsed = _parsed_workspace(urn)
    if parsed is None or parsed[1] == _GLOBAL_NAMESPACE:
        return workspace_id
    return parsed[1]


async def _write_parent(
    client: PermissionClient,
    *,
    resource_type: str,
    urn: str,
    tenant_id: str,
    workspace_id: str,
    project_urn: Optional[str],
) -> None:
    await client.write_relationship(
        resource_type=resource_type,
        resource_urn=urn,
        relation="parent_workspace",
        subject_type="workspace",
        subject_urn=workspace_urn(tenant_id, _parent_workspace_id(urn, workspace_id)),
    )
    await client.set_single_subject(
        resource_type=resource_type,
        resource_urn=urn,
        relation="parent_project",
        subject_type="project",
        subject_urn=project_urn or None,
    )


async def discover_seed_pairs(
    pool: asyncpg.Pool, default_tenant: str, default_workspace: str
) -> set[tuple[str, str]]:
    """(tenant, business workspace) pairs to backfill, including the boot pair."""
    pairs = {(default_tenant, default_workspace)}
    scoped_by_tenant: dict[str, set[str]] = {}
    for row in await pool.fetch(_SCOPED_URNS_SQL):
        parsed = _parsed_workspace(row["urn"])
        if parsed is None or parsed[1] == _GLOBAL_NAMESPACE:
            continue
        scoped_by_tenant.setdefault(parsed[0], set()).add(parsed[1])
        pairs.add(parsed)
    for row in await pool.fetch(_TENANT_SQL):
        tenant = row["tenant_id"]
        if tenant in scoped_by_tenant or tenant == default_tenant:
            continue
        pairs.add((tenant, default_workspace))
    return pairs


async def ensure_authz_seeded(
    client: PermissionClient,
    tenant_id: str,
    workspace_id: str,
    pool: Optional[asyncpg.Pool] = None,
    *,
    global_parent_workspace: Optional[str] = None,
) -> None:
    """Write ontology relationship tuples. Identity owns the SpiceDB schema.

    ``global_parent_workspace`` is the one business workspace that receives
    tenant-global resources (shared property types, value types). It defaults
    to ``workspace_id``, which is what a single-pair call wants.
    """
    if pool is None:
        return
    global_parent = global_parent_workspace or workspace_id

    def _take(urn: str) -> bool:
        return _include_resource(urn, tenant_id, workspace_id, global_parent=global_parent)

    for object_type in await list_object_types(pool, tenant_id):
        urn = object_type["urn"]
        if not _take(urn):
            continue
        await _write_parent(
            client,
            resource_type="object_type",
            urn=urn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            project_urn=object_type.get("project_urn"),
        )
    for spt in await list_shared_property_types(pool, tenant_id):
        urn = spt.get("urn") or shared_property_type_urn(tenant_id, spt["api_name"])
        if not _take(urn):
            continue
        await _write_parent(
            client,
            resource_type="shared_property_type",
            urn=urn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            project_urn=spt.get("project_urn"),
        )
    for relation in await list_relation_types(pool, tenant_id):
        urn = relation["urn"]
        if not _take(urn):
            continue
        await _write_parent(
            client,
            resource_type="relation_type",
            urn=urn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            project_urn=relation.get("project_urn"),
        )
    for value_type in await list_value_types(pool, tenant_id):
        urn = value_type.get("urn") or value_type_urn(tenant_id, value_type["name"])
        if not _take(urn):
            continue
        await _write_parent(
            client,
            resource_type="value_type",
            urn=urn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            project_urn=value_type.get("project_urn"),
        )


def global_parent_workspace(workspaces: set[str], default_workspace: str) -> str:
    """Business workspace that receives tenant-global resources.

    Prefer the process default when this tenant actually has it. A tenant
    with a single other workspace keeps that one. Several workspaces and
    no designated default stay on ``default_workspace`` — the alphabetical
    first workspace is not a parent.
    """
    if default_workspace in workspaces or len(workspaces) != 1:
        if len(workspaces) > 1 and default_workspace not in workspaces:
            logger.warning(
                "tenant-global resources have no designated workspace among %s; using %s",
                sorted(workspaces),
                default_workspace,
            )
        return default_workspace
    return next(iter(workspaces))


async def ensure_authz_seeded_all(
    client: PermissionClient,
    pool: asyncpg.Pool,
    default_tenant: str,
    default_workspace: str,
) -> None:
    """Backfill every filiale already stored, not only the process tenant."""
    pairs = await discover_seed_pairs(pool, default_tenant, default_workspace)
    by_tenant: dict[str, set[str]] = {}
    for tenant, workspace in pairs:
        by_tenant.setdefault(tenant, set()).add(workspace)
    global_parent_by_tenant = {
        tenant: global_parent_workspace(workspaces, default_workspace)
        for tenant, workspaces in by_tenant.items()
    }
    for tenant, workspace in sorted(pairs):
        await ensure_authz_seeded(
            client,
            tenant,
            workspace,
            pool,
            global_parent_workspace=global_parent_by_tenant[tenant],
        )
