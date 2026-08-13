"""The Action approval workflow — approve/reject/compensate lifecycle for
high-risk Actions, and the internal saga-compensation endpoint Automation's
Workflow Engine calls on Step-2 failure. Actual Action invocation is
generic over any ObjectType (`routers/objects/generic.py`).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from holon_common import Principal, build_urn

from .. import actions
from .. import core

router = APIRouter()


async def _approval_object_type_urn(approval: dict) -> str:
    """Get the target ObjectType URN for an approval request."""
    definition = await actions._get_action_definition(core.pool, approval["tenant_id"], approval["action_name"])
    return await core._object_type_urn_for(definition["target_object_type"], tenant_id=approval["tenant_id"])


class ApprovalDecisionRequest(BaseModel):
    note: Optional[str] = None


class CompensationRequest(BaseModel):
    error: str


def _require_workflow_engine(principal: Principal) -> None:
    """Verify that the principal is the internal Workflow Engine service account."""
    expected_urn = build_urn(core.TENANT_ID, "global", "service-account", actions.WORKFLOW_ENGINE_URN_NAME)
    if principal.type != "service_account" or principal.urn != expected_urn:
        raise HTTPException(
            status_code=403,
            detail="compensate is restricted to Automation's Workflow Engine — it is not a client-facing endpoint",
        )


@router.post("/internal/approvals/{approval_id}/compensate")
async def compensate_approval(
    approval_id: int, request: CompensationRequest, principal: Principal = Depends(core.current_principal)
) -> dict:
    """Compensate/revert an approval step invoked by Workflow Engine."""
    _require_workflow_engine(principal)
    try:
        return await actions.compensate_from_workflow_engine(
            core.pool, approval_id=approval_id, workspace_id=core.WORKSPACE_ID, error=request.error
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/approvals/{approval_id}/approve")
async def approve_approval(
    approval_id: int, request: ApprovalDecisionRequest, principal: Principal = Depends(core.current_principal)
) -> dict:
    approval = await actions.get_approval(core.pool, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"approval {approval_id} not found")
    await core._authorize_object_type(principal, await _approval_object_type_urn(approval), "approve")
    try:
        return await actions.approve_action(
            core.pool,
            approval_id=approval_id,
            workspace_id=core.WORKSPACE_ID,
            decider=principal,
            note=request.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/approvals/{approval_id}/reject")
async def reject_approval(
    approval_id: int, request: ApprovalDecisionRequest, principal: Principal = Depends(core.current_principal)
) -> dict:
    approval = await actions.get_approval(core.pool, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"approval {approval_id} not found")
    await core._authorize_object_type(principal, await _approval_object_type_urn(approval), "approve")
    try:
        return await actions.reject_action(
            core.pool, approval_id=approval_id, workspace_id=core.WORKSPACE_ID, decider=principal, note=request.note
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/approvals/{approval_id}")
async def get_approval_by_id(approval_id: int, principal: Principal = Depends(core.current_principal)) -> dict:
    approval = await actions.get_approval(core.pool, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"approval {approval_id} not found")
    await core._authorize_object_type(principal, await _approval_object_type_urn(approval), "read")
    return approval


@router.get("/approvals")
async def list_pending_approvals(
    status: Optional[str] = None, principal: Principal = Depends(core.current_principal)
) -> list[dict]:
    """List approvals the caller is authorized to read on the target ObjectType."""
    rows = await actions.list_approvals(core.pool, principal.tenant_id, status=status)
    visible: list[dict] = []
    for row in rows:
        try:
            ot_urn = await _approval_object_type_urn(row)
        except Exception:
            continue
        if await core._is_authorized_read(principal, ot_urn):
            visible.append(row)
    return visible
