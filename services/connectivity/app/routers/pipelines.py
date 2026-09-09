"""Connectivity pipelines routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from holon_common import EventActor, HolonError, Principal, build_urn
from holon_common.audit import emit_audit

from .. import deps, pipeline
from ..deps import _authorize_workspace, current_principal
from ..ingest import CreatePipelineRequest, SetPipelineScheduleRequest, _run_pipeline


router = APIRouter()


@router.post("/pipelines/{name}", status_code=201)
async def create_pipeline(
    name: str, request: CreatePipelineRequest, principal: Principal = Depends(current_principal)
) -> dict:
    await _authorize_workspace(principal, "write")
    try:
        return await pipeline.create_pipeline(
            deps.pool,
            tenant_id=principal.tenant_id,
            name=name,
            steps=[step.model_dump() for step in request.steps],
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('PipelineValidationFailed', str(exc)) from exc



@router.get("/pipelines")
async def list_pipelines(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await pipeline.list_pipelines(deps.pool, principal.tenant_id)



@router.get("/pipelines/{name}")
async def get_pipeline(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "read")
    definition = await pipeline.get_pipeline(deps.pool, name)
    if definition is None:
        raise HolonError.not_found('PipelineNotFound', f"unknown pipeline: {name}", name=name)
    return definition



@router.delete("/pipelines/{name}")
async def delete_pipeline(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    deleted = await pipeline.delete_pipeline(
        deps.pool, tenant_id=principal.tenant_id, name=name
    )
    if not deleted:
        raise HolonError.not_found('PipelineNotFound', f"unknown pipeline: {name}", name=name)
    return {"deleted": name}



@router.get("/pipelines/{name}/runs")
async def list_pipeline_runs(name: str, principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await pipeline.list_runs(deps.pool, principal.tenant_id, name)



@router.post("/pipelines/{name}/run")
async def run_pipeline(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    actor = EventActor(type=principal.type, urn=principal.urn, on_behalf_of=principal.on_behalf_of)
    return await _run_pipeline(name, actor=actor, tenant_id=principal.tenant_id)



@router.post("/pipelines/{name}/schedule")
async def set_pipeline_schedule(name: str, body: SetPipelineScheduleRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Set or clear background execution schedule for a pipeline."""
    await _authorize_workspace(principal, "write")
    if body.schedule_interval_minutes is not None and body.schedule_interval_minutes <= 0:
        raise HolonError.invalid_argument(
            "InvalidSchedule", "schedule_interval_minutes must be a positive number of minutes"
        )
    if await pipeline.get_pipeline(deps.pool, name) is None:
        raise HolonError.not_found('PipelineNotFound', f"unknown pipeline: {name}", name=name)
    result = await pipeline.set_pipeline_schedule(deps.pool, name, body.schedule_interval_minutes)
    emit_audit(
        category="access",
        action="connectivity.pipeline.schedule_updated",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="pipeline",
        resource_urn=build_urn(principal.tenant_id, "global", "pipeline", name),
        extra={"schedule_interval_minutes": body.schedule_interval_minutes},
    )
    return result

