"""Automation durable audit trail."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from holon_common import Principal
from holon_common.service_runtime import list_audit_events_http

from .. import deps
from ..deps import authorize_workspace, current_principal

router = APIRouter()


@router.get("/audit-events")
async def list_automation_audit_events(
    principal: Principal = Depends(current_principal),
    category: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    outcome: str | None = None,
    traceId: str | None = None,
    pageSize: int | None = None,
    pageToken: str | None = None,
) -> dict:
    """Durable Automation audit (workflows, agent-chain triggers)."""
    await authorize_workspace(principal, "approve")
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
