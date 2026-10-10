"""Intelligence durable audit trail."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from holon_common import Principal
from holon_common.service_runtime import list_audit_events_http

from .. import deps
from ..deps import _authorize_workspace, current_principal

router = APIRouter()


@router.get("/audit-events")
async def list_intelligence_audit_events(
    principal: Principal = Depends(current_principal),
    category: Optional[str] = None,
    action: Optional[str] = None,
    actor: Optional[str] = None,
    outcome: Optional[str] = None,
    traceId: Optional[str] = None,
    pageSize: Optional[int] = None,
    pageToken: Optional[str] = None,
) -> dict:
    """Durable Intelligence audit (sessions, tool plugins)."""
    await _authorize_workspace(principal, "approve")
    return await list_audit_events_http(
        deps.pool,
        principal.tenant_id,
        category=category,
        action=action,
        actor=actor,
        outcome=outcome,
        traceId=traceId,
        pageSize=pageSize,
        pageToken=pageToken,
    )


