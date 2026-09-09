"""Connectivity admin routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from holon_common import Principal, build_urn, require_tenant_match
from holon_common.audit import emit_audit
from holon_common.audit_store import list_events_page

from .. import deps
from ..deps import TENANT_ID, _authorize_workspace, current_principal
from ..ingest import QuiesceRequest


router = APIRouter()


@router.get("/audit-events")
async def list_connectivity_audit_events(
    principal: Principal = Depends(current_principal),
    category: Optional[str] = None,
    action: Optional[str] = None,
    actor: Optional[str] = None,
    outcome: Optional[str] = None,
    pageSize: Optional[int] = None,
    pageToken: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> dict:
    """Durable Connectivity audit (syncs, plugins, sources, quiesce)."""
    await _authorize_workspace(principal, "approve", workspace_id=workspace_id)
    return await list_events_page(
        deps.pool,
        principal.tenant_id,
        category=category,
        action=action,
        actor_urn=actor,
        outcome=outcome,
        page_size=50 if pageSize is None else pageSize,
        page_token=pageToken,
    )



@router.post("/admin/quiesce")
async def admin_quiesce(body: QuiesceRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Toggle scheduled ingestion quiesce status across replicas."""
    require_tenant_match(principal, TENANT_ID)  # bootstrap admins only for instance quiesce
    await deps.pool.execute(
        """
        INSERT INTO connectivity_runtime (key, value, updated_at) VALUES ('quiesced', $1, now())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """,
        "true" if body.quiesced else "false",
    )
    emit_audit(
        category="access",
        action="connectivity.quiesce",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="connectivity_runtime",
        resource_urn=build_urn(principal.tenant_id, "global", "connectivity-runtime", "quiesced"),
        extra={"quiesced": body.quiesced},
    )
    return {"quiesced": body.quiesced}

