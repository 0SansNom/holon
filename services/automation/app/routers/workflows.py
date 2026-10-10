"""Workflow execution lookups."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from holon_common import HolonError, Principal

from .. import deps, workflow
from ..deps import authorize_workspace, current_principal

router = APIRouter()


@router.get("/workflows/{approval_id}")
async def get_workflow_execution(
    approval_id: int, principal: Principal = Depends(current_principal)
) -> dict:
    """Fetch workflow execution record by approval ID."""
    await authorize_workspace(principal, "read")
    execution = await workflow.get_workflow_execution(deps.pool, approval_id)
    if execution is None or execution.get("tenant_id") != principal.tenant_id:
        raise HolonError.not_found(
            "WorkflowExecutionNotFound",
            f"no workflow execution found for approval {approval_id}",
            approval_id=approval_id,
        )
    return execution
