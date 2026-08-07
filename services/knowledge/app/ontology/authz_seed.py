"""SpiceDB bootstrap for the ontology's own resources — Knowledge owns
ObjectType, so it links its own resources under the workspace itself;
Identity only owns the tenant/workspace side of the graph.
"""

from __future__ import annotations

from holon_common.authz import PermissionClient

from .urns import object_type_urn, workspace_urn


async def ensure_authz_seeded(client: PermissionClient, schema_path: str, tenant_id: str, workspace_id: str) -> None:
    """`write_schema` is idempotent: calling it again with the same file
    is a no-op, and removes any dependency on Identity having started
    first (see `PermissionClient` docstring in `identity/app/main.py`).
    """
    from pathlib import Path

    await client.write_schema(Path(schema_path).read_text())
    w_urn = workspace_urn(tenant_id, workspace_id)
    for name in ("Customer", "Order", "SupportTicket", "ProductReview", "Supplier", "InventoryLevel"):
        await client.write_relationship(
            resource_type="object_type",
            resource_urn=object_type_urn(tenant_id, workspace_id, name),
            relation="parent_workspace",
            subject_type="workspace",
            subject_urn=w_urn,
        )
