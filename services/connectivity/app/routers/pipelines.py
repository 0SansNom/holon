"""Connectivity pipelines routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, Query

from holon_common import EventActor, HolonError, Principal
from holon_common.audit import emit_audit

from .. import deps, pipeline
from ..deps import (
    _authorize_pipeline,
    _authorize_workspace,
    _filter_readable,
    _resolve_workspace,
    _seed_pipeline_authz,
    _unlink_resource_authz,
    current_principal,
    pipeline_urn,
    resource_workspace,
)
from ..ingest import CreatePipelineRequest, SetPipelineScheduleRequest, _run_pipeline


router = APIRouter()


async def _get_authorized(principal: Principal, name: str, permission: str) -> dict:
    definition = await pipeline.get_pipeline(deps.pool, principal.tenant_id, name)
    if definition is None:
        raise HolonError.not_found('PipelineNotFound', f"unknown pipeline: {name}", name=name)
    await _authorize_pipeline(
        principal, permission, name=name, workspace_id=resource_workspace(definition)
    )
    return definition


@router.post("/pipelines/{name}", status_code=201)
async def create_pipeline(
    name: str,
    request: CreatePipelineRequest,
    principal: Principal = Depends(current_principal),
    workspace_id: Optional[str] = Query(None, alias="workspaceId"),
    x_holon_workspace_id: Optional[str] = Header(None, alias="X-Holon-Workspace-Id"),
) -> dict:
    target_workspace = _resolve_workspace(
        explicit=request.workspace_id,
        workspace_id=workspace_id,
        x_holon_workspace_id=x_holon_workspace_id,
    )
    await _authorize_workspace(principal, "write", workspace_id=target_workspace)
    existing = await pipeline.get_pipeline(deps.pool, principal.tenant_id, name)
    if existing is not None:
        stored = resource_workspace(existing)
        await _authorize_pipeline(principal, "write", name=name, workspace_id=stored)
        if stored != target_workspace:
            raise HolonError.invalid_argument(
                "PipelineWorkspaceMismatch",
                f"pipeline {name!r} is bound to workspace {stored!r}; got {target_workspace!r}",
                name=name,
                expected_workspace_id=stored,
                got_workspace_id=target_workspace,
            )
    try:
        created = await pipeline.create_pipeline(
            deps.pool,
            tenant_id=principal.tenant_id,
            name=name,
            workspace_id=target_workspace,
            steps=[step.model_dump() for step in request.steps],
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('PipelineValidationFailed', str(exc)) from exc
    await _seed_pipeline_authz(
        tenant_id=principal.tenant_id,
        workspace_id=target_workspace,
        name=name,
        compensate_delete=None
        if existing is not None
        else lambda: pipeline.delete_pipeline(deps.pool, tenant_id=principal.tenant_id, name=name),
    )
    return created


@router.get("/pipelines")
async def list_pipelines(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await _filter_readable(
        principal, "pipeline", await pipeline.list_pipelines(deps.pool, principal.tenant_id)
    )


@router.get("/pipelines/{name}")
async def get_pipeline(name: str, principal: Principal = Depends(current_principal)) -> dict:
    return await _get_authorized(principal, name, "read")


@router.delete("/pipelines/{name}")
async def delete_pipeline(name: str, principal: Principal = Depends(current_principal)) -> dict:
    definition = await _get_authorized(principal, name, "write")
    deleted = await pipeline.delete_pipeline(
        deps.pool, tenant_id=principal.tenant_id, name=name
    )
    if not deleted:
        raise HolonError.not_found('PipelineNotFound', f"unknown pipeline: {name}", name=name)
    await _unlink_resource_authz(
        "pipeline",
        tenant_id=principal.tenant_id,
        workspace_id=resource_workspace(definition),
        name=name,
    )
    return {"deleted": name}


@router.get("/pipelines/{name}/runs")
async def list_pipeline_runs(name: str, principal: Principal = Depends(current_principal)) -> list[dict]:
    await _get_authorized(principal, name, "read")
    return await pipeline.list_runs(deps.pool, principal.tenant_id, name)


@router.post("/pipelines/{name}/run")
async def run_pipeline(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _get_authorized(principal, name, "write")
    actor = EventActor(type=principal.type, urn=principal.urn, on_behalf_of=principal.on_behalf_of)
    return await _run_pipeline(name, actor=actor, tenant_id=principal.tenant_id)


@router.post("/pipelines/{name}/schedule")
async def set_pipeline_schedule(name: str, body: SetPipelineScheduleRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Set or clear background execution schedule for a pipeline."""
    if body.schedule_interval_minutes is not None and body.schedule_interval_minutes <= 0:
        raise HolonError.invalid_argument(
            "InvalidSchedule", "schedule_interval_minutes must be a positive number of minutes"
        )
    definition = await _get_authorized(principal, name, "write")
    result = await pipeline.set_pipeline_schedule(
        deps.pool, principal.tenant_id, name, body.schedule_interval_minutes
    )
    emit_audit(
        category="access",
        action="connectivity.pipeline.schedule_updated",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="pipeline",
        resource_urn=pipeline_urn(principal.tenant_id, resource_workspace(definition), name),
        extra={"schedule_interval_minutes": body.schedule_interval_minutes},
    )
    return result
