"""Customer Actions and their approval workflow — `putOnCreditHold`/
`closeAccount`, the approve/reject/compensate lifecycle those trigger for
high-risk Actions, and the internal saga-compensation endpoint Automation's
Workflow Engine calls on Step-2 failure.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from holon_common import Principal, build_urn

from .. import actions, resolver
from .. import core

router = APIRouter()


async def _approval_object_type_urn(approval: dict) -> str:
    """`_get_action_definition` first (`ACTION_DEFINITIONS`, then the
    declarative `action_type` registry — see its own docstring), then
    `core._object_type_urn_for` (the six seeded types, falling back to a
    real query for a self-serve one) instead of the two static dict
    lookups this used to be — both needed generalizing for a declarative
    Action Type's approval to be authorizable at all.
    """
    definition = await actions._get_action_definition(core.pool, approval["tenant_id"], approval["action_name"])
    return await core._object_type_urn_for(definition["target_object_type"], tenant_id=approval["tenant_id"])


class ActionRequest(BaseModel):
    reason: str
    ttl_seconds: Optional[int] = None
    """Test/demo-only override of the default 24h approval review window
    — same treatment as Connectivity's
    `CLOSE_ACCOUNT_FAILURE_SENTINEL`. Ignored entirely for low-risk Actions,
    which never create an `action_approval` row in the first place.
    """


class ApprovalDecisionRequest(BaseModel):
    note: Optional[str] = None


class CompensationRequest(BaseModel):
    error: str


async def _invoke_customer_action(
    action_name: str, customer_id: int, principal: Principal, reason: str, ttl_seconds: Optional[int] = None
) -> dict:
    """The one entry point every Customer Action's endpoint calls — same
    permission check, same 404 handling, same PDP path. Whether it applies
    immediately or waits for approval is decided entirely inside
    `actions.request_action`, driven by the Action's `risk_level`, not by
    which endpoint got hit.
    """
    definition = actions.ACTION_DEFINITIONS[action_name]
    await core._authorize_object_type(principal, core.CUSTOMER_OBJECT_TYPE_URN, definition["required_permission"])
    if not await core._resolve_one("Customer", principal.tenant_id, customer_id, resolver.fetch_customers, "customer_id", principal=principal):
        raise HTTPException(status_code=404, detail=f"Customer/{customer_id} not found")
    return await actions.request_action(
        core.pool,
        action_name=action_name,
        tenant_id=principal.tenant_id,
        workspace_id=core.WORKSPACE_ID,
        customer_id=customer_id,
        principal=principal,
        reason=reason,
        ttl_seconds=ttl_seconds,
    )


@router.post("/objects/Customer/{customer_id}/actions/putOnCreditHold")
async def put_customer_on_credit_hold(
    customer_id: int, request: ActionRequest, principal: Principal = Depends(core.current_principal)
) -> dict:
    return await _invoke_customer_action(
        "Customer.putOnCreditHold", customer_id, principal, request.reason, request.ttl_seconds
    )


@router.post("/objects/Customer/{customer_id}/actions/closeAccount")
async def close_customer_account(
    customer_id: int, request: ActionRequest, principal: Principal = Depends(core.current_principal)
) -> dict:
    """High risk (deletion-class): this only ever returns
    `pending_approval`. The mutation happens in `approve_approval` below,
    gated by the `approve` permission (workspace admin only).
    """
    return await _invoke_customer_action(
        "Customer.closeAccount", customer_id, principal, request.reason, request.ttl_seconds
    )


def _require_workflow_engine(principal: Principal) -> None:
    """Same shape as Connectivity's own `_require_saga_orchestrator` — the
    internal compensation endpoint below reverts Step 1 of a saga,
    and must never be reachable by an ordinary authenticated
    caller, only by Automation's Workflow Engine.
    """
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
    """Called by Automation's Workflow Engine when its own Step 2 (the
    external write) fails — Knowledge reverts Step 1 of the saga itself,
    since Automation can't reach this service's own tables
    directly. See `actions.compensate_from_workflow_engine`.
    """
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
    await core._authorize_object_type(principal, core.CUSTOMER_OBJECT_TYPE_URN, "read")
    return await actions.list_approvals(core.pool, principal.tenant_id, status=status)
