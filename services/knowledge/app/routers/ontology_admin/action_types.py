"""Action Type definitions."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from pyiceberg.exceptions import NoSuchTableError

from holon_common import EventActor, EventEnvelope, HolonError, Principal, build_urn

from ... import actions, catalog, glossary, ontology, ontology_health, query_log, resolver
from ... import core
from ...paging import interface_instance_key
from ..objects.paging_deps import page_response, paging_query
from ._auth import (
    IDENTITY_URL,
    _ALLOWED_CLASSIFICATIONS,
    _authorize_marking_administer,
    _authorize_ontology_governance,
    _authorize_ontology_write,
    _authorize_relation_type,
    _authorize_shared_property_type,
    _authorize_value_type,
    _authorize_workspace_read,
    _identity_validation_token,
    _link_object_type_to_project,
    _link_relation_type_to_project,
    _link_shared_property_type_to_project,
    _link_value_type_to_project,
    _seed_relation_type_authz,
    _seed_shared_property_type_authz,
    _seed_value_type_authz,
    _validate_optional_project_urn,
    _validate_resource_type,
)

router = APIRouter()
logger = logging.getLogger("knowledge.ontology_admin")


class ActionTypeRequest(BaseModel):
    name: str
    target_object_type: Optional[str] = None
    target_interface: Optional[str] = None
    required_permission: str
    risk_level: str
    description: str
    parameters: list[dict] = []
    edits: list[dict] = []
    submission_criteria: list[dict] = []
    function_side_effect: Optional[str] = None
    writeback_dataset: Optional[str] = None
    edit_function: Optional[str] = None
    sections: list[dict] = []
    type_classes: Optional[list[str]] = None
    lifecycle_status: str = "experimental"
    deprecation_reason: Optional[str] = None
    deprecation_deadline: Optional[str] = None
    replacement_urn: Optional[str] = None
    notify_webhook: Optional[str] = None


@router.post("/action-types", status_code=201)
async def create_action_type(request: ActionTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    """Registering an Action Type is ontology governance, same tier as
    an Interface or a Value Type — the workspace's own `approve`
    permission. The no-code counterpart to writing a Python
    `register_apply_function`-decorated handler in `actions.py`: no
    deploy needed to add a new kind of write, just configuration —
    validated structurally here (real references, like a parameter's
    `value_type` or `target_object_type` existing, are checked at
    invocation time by `actions.request_generic_action`, not here).
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_action_type(core.pool, principal.tenant_id, request.name) is not None:
        raise HolonError.conflict('ActionTypeAlreadyExists', f"action type already exists: {request.name}")
    try:
        return await ontology.create_action_type(
            core.pool,
            tenant_id=principal.tenant_id,
            name=request.name,
            target_object_type=request.target_object_type,
            target_interface=request.target_interface,
            required_permission=request.required_permission,
            risk_level=request.risk_level,
            description=request.description,
            parameters=request.parameters,
            edits=request.edits,
            submission_criteria=request.submission_criteria,
            function_side_effect=request.function_side_effect,
            writeback_dataset=request.writeback_dataset,
            edit_function=request.edit_function,
            sections=request.sections,
            type_classes=request.type_classes,
            lifecycle_status=request.lifecycle_status,
            deprecation_reason=request.deprecation_reason,
            deprecation_deadline=request.deprecation_deadline,
            replacement_urn=request.replacement_urn,
            notify_webhook=request.notify_webhook,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('ActionTypeValidationFailed', str(exc)) from exc


@router.get("/action-types")
async def list_action_types(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    return await ontology.list_action_types(core.pool, principal.tenant_id)


@router.get("/action-types/{name}")
async def get_action_type(name: str, principal: Principal = Depends(core.current_principal)) -> dict:
    action_type = await ontology.get_action_type(core.pool, principal.tenant_id, name)
    if action_type is None:
        raise HolonError.not_found('ActionTypeNotFound', f"unknown action type: {name}")
    return action_type


@router.get("/action-types/{name}/observability")
async def get_action_type_observability(
    name: str,
    days: int = Query(default=30, ge=1, le=90),
    principal: Principal = Depends(core.current_principal),
) -> dict:
    """Foundry-shaped Action Observability: invocation + approval counts from Holon tables.

    Prometheus counters (`holon_action_events_total`) stay for ops scrapers; this endpoint
    is what Ontology Manager can chart without a metrics backend.
    """
    action_type = await ontology.get_action_type(core.pool, principal.tenant_id, name)
    if action_type is None:
        raise HolonError.not_found('ActionTypeNotFound', f"unknown action type: {name}")

    inv = await core.pool.fetchrow(
        """
        SELECT
          COUNT(*)::int AS invocations,
          COUNT(*) FILTER (WHERE reverted_at IS NOT NULL)::int AS reverted,
          COUNT(*) FILTER (WHERE edits IS NOT NULL)::int AS with_edits
        FROM action_invocation
        WHERE tenant_id = $1
          AND action_name = $2
          AND invoked_at >= now() - ($3::int * interval '1 day')
        """,
        principal.tenant_id,
        name,
        days,
    )
    approvals = await core.pool.fetchrow(
        """
        SELECT
          COUNT(*) FILTER (WHERE status = 'pending')::int AS pending,
          COUNT(*) FILTER (WHERE status = 'approved')::int AS approved,
          COUNT(*) FILTER (WHERE status = 'rejected')::int AS rejected,
          COUNT(*) FILTER (WHERE status = 'expired')::int AS expired
        FROM action_approval
        WHERE tenant_id = $1
          AND action_name = $2
          AND requested_at >= now() - ($3::int * interval '1 day')
        """,
        principal.tenant_id,
        name,
        days,
    )
    by_day = await core.pool.fetch(
        """
        SELECT to_char(date_trunc('day', invoked_at), 'YYYY-MM-DD') AS day,
               COUNT(*)::int AS invocations
        FROM action_invocation
        WHERE tenant_id = $1
          AND action_name = $2
          AND invoked_at >= now() - ($3::int * interval '1 day')
        GROUP BY 1
        ORDER BY 1
        """,
        principal.tenant_id,
        name,
        days,
    )
    return {
        "action_name": name,
        "days": days,
        "invocations": inv["invocations"] if inv else 0,
        "reverted": inv["reverted"] if inv else 0,
        "with_edits": inv["with_edits"] if inv else 0,
        "approvals": {
            "pending": approvals["pending"] if approvals else 0,
            "approved": approvals["approved"] if approvals else 0,
            "rejected": approvals["rejected"] if approvals else 0,
            "expired": approvals["expired"] if approvals else 0,
        },
        "by_day": [{"day": r["day"], "invocations": r["invocations"]} for r in by_day],
    }


@router.put("/action-types/{name}")
async def update_action_type(
    name: str, request: ActionTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """`create_action_type`'s SQL is already `ON CONFLICT (tenant_id,
    name) DO UPDATE` — this endpoint is a full-replace edit (matches the
    fact every column but `tenant_id`/`name` is already upsert-shaped
    underneath), gated on the same existence direction `create` isn't:
    404 if it's *not* there yet, instead of `create`'s 409 if it is.
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_action_type(core.pool, principal.tenant_id, name) is None:
        raise HolonError.not_found('ActionTypeNotFound', f"unknown action type: {name}")
    try:
        return await ontology.create_action_type(
            core.pool,
            tenant_id=principal.tenant_id,
            name=name,
            target_object_type=request.target_object_type,
            target_interface=request.target_interface,
            required_permission=request.required_permission,
            risk_level=request.risk_level,
            description=request.description,
            parameters=request.parameters,
            edits=request.edits,
            submission_criteria=request.submission_criteria,
            function_side_effect=request.function_side_effect,
            writeback_dataset=request.writeback_dataset,
            edit_function=request.edit_function,
            sections=request.sections,
            type_classes=request.type_classes,
            lifecycle_status=request.lifecycle_status,
            deprecation_reason=request.deprecation_reason,
            deprecation_deadline=request.deprecation_deadline,
            replacement_urn=request.replacement_urn,
            notify_webhook=request.notify_webhook,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('ActionTypeValidationFailed', str(exc)) from exc
