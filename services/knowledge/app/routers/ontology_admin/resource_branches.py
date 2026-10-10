"""Branches on non-ObjectType ontology resources."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from holon_common import HolonError, Principal

from ... import ontology
from ... import core
from ._auth import (
    _authorize_ontology_governance,
    _authorize_ontology_write,
    _validate_resource_type,
)

router = APIRouter()
logger = logging.getLogger("knowledge.ontology_admin")


class CreateResourceBranchRequest(BaseModel):
    branch_name: str
    proposed_definition: dict


class UpdateResourceBranchDraftRequest(BaseModel):
    proposed_definition: dict


class ReviewBranchRequest(BaseModel):
    decision: str  # "approved" | "changes_requested"
    note: Optional[str] = None


@router.post("/ontology-resources/{resource_type}/{resource_name}/branches", status_code=201)
async def create_resource_branch(
    resource_type: str, resource_name: str, request: CreateResourceBranchRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """Generic branch/review for the 4 registries that aren't ObjectType
    (`ontology/resource_branching.py`) — same `write`-tier gate
    `create_branch` (ObjectType's own version) already uses. Unlike
    ObjectType, there's no structural validation of `proposed_definition`
    at this point — it's a freeform dict until review time, when it's
    validated for real by calling straight through to the target
    registry's `create_*`/`update_*`.
    """
    _validate_resource_type(resource_type)
    await _authorize_ontology_write(principal, workspace_id)
    try:
        return await ontology.create_resource_branch(
            core.pool,
            resource_type=resource_type,
            resource_name=resource_name,
            branch_name=request.branch_name,
            created_by_urn=principal.urn,
            tenant_id=principal.tenant_id,
            proposed_definition=request.proposed_definition,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('BranchValidationFailed', str(exc)) from exc


@router.get("/ontology-resources/{resource_type}/{resource_name}/branches")
async def list_resource_branches(
    resource_type: str, resource_name: str, principal: Principal = Depends(core.current_principal)
) -> list[dict]:
    _validate_resource_type(resource_type)
    return await ontology.list_resource_branches(
        core.pool, resource_type=resource_type, resource_name=resource_name, tenant_id=principal.tenant_id
    )


@router.get("/ontology-resources/{resource_type}/{resource_name}/branches/{branch_name}")
async def get_resource_branch(
    resource_type: str, resource_name: str, branch_name: str, principal: Principal = Depends(core.current_principal)
) -> dict:
    _validate_resource_type(resource_type)
    branch = await ontology.get_resource_branch(
        core.pool, resource_type=resource_type, resource_name=resource_name, branch_name=branch_name, tenant_id=principal.tenant_id
    )
    if branch is None:
        raise HolonError.not_found('BranchNotFound', f"unknown branch: {branch_name}")
    return branch


@router.post("/ontology-resources/{resource_type}/{resource_name}/branches/{branch_name}/draft")
async def update_resource_branch_draft(
    resource_type: str,
    resource_name: str,
    branch_name: str,
    request: UpdateResourceBranchDraftRequest,
    principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace),
) -> dict:
    _validate_resource_type(resource_type)
    await _authorize_ontology_write(principal, workspace_id)
    try:
        return await ontology.update_resource_branch_draft(
            core.pool,
            resource_type=resource_type,
            resource_name=resource_name,
            branch_name=branch_name,
            tenant_id=principal.tenant_id,
            proposed_definition=request.proposed_definition,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('BranchValidationFailed', str(exc)) from exc


@router.post("/ontology-resources/{resource_type}/{resource_name}/branches/{branch_name}/review")
async def review_resource_branch(
    resource_type: str,
    resource_name: str,
    branch_name: str,
    request: ReviewBranchRequest,
    principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """The merge gate — workspace `approve` (admin-only), same tier as
    `review_branch` (ObjectType's own version). `decision == "approved"`
    calls straight through to the target registry's real `create_*`/
    `update_*`, so every existing structural validation those functions
    already do still applies unchanged.
    """
    _validate_resource_type(resource_type)
    await _authorize_ontology_governance(principal, workspace_id)
    try:
        return await ontology.review_resource_branch(
            core.pool,
            resource_type=resource_type,
            resource_name=resource_name,
            branch_name=branch_name,
            reviewer_urn=principal.urn,
            decision=request.decision,
            note=request.note,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('BranchValidationFailed', str(exc)) from exc


@router.get("/ontology-resources/{resource_type}/{resource_name}/branches/{branch_name}/reviews")
async def list_resource_branch_reviews(
    resource_type: str, resource_name: str, branch_name: str, principal: Principal = Depends(core.current_principal)
) -> list[dict]:
    _validate_resource_type(resource_type)
    branch = await ontology.get_resource_branch(
        core.pool, resource_type=resource_type, resource_name=resource_name, branch_name=branch_name, tenant_id=principal.tenant_id
    )
    if branch is None:
        raise HolonError.not_found('BranchNotFound', f"unknown branch: {branch_name}")
    return await ontology.list_resource_branch_reviews(core.pool, branch["id"])
