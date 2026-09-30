from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from holon_common import Principal, parse_urn

from .. import deps, project_pins, resource_tags
from ..deps import (
    _RESOURCE_AUTHZ_TYPE,
    _authorize_project,
    _authorize_resource,
    _filter_readable_resource_urns,
    _resource_authz_type,
    current_principal,
)

router = APIRouter()


class SetTagsRequest(BaseModel):
    tags: list[str]


@router.put("/api/resources/{urn}/tags")
async def set_resource_tags(
    urn: str, body: SetTagsRequest, principal: Principal = Depends(current_principal)
) -> dict:
    await _authorize_resource(principal, _resource_authz_type(urn), urn, "write")
    return await resource_tags.set_tags(
        deps.pool, tenant_id=principal.tenant_id, resource_urn=urn, tags=body.tags, updated_by_urn=principal.urn,
    )


@router.post("/api/resources/{urn}/featured")
async def feature_resource(urn: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_resource(principal, _resource_authz_type(urn), urn, "write")
    return await resource_tags.set_featured(
        deps.pool, tenant_id=principal.tenant_id, resource_urn=urn, featured=True, updated_by_urn=principal.urn,
    )


@router.delete("/api/resources/{urn}/featured")
async def unfeature_resource(urn: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_resource(principal, _resource_authz_type(urn), urn, "write")
    return await resource_tags.set_featured(
        deps.pool, tenant_id=principal.tenant_id, resource_urn=urn, featured=False, updated_by_urn=principal.urn,
    )


@router.get("/api/resources")
async def list_resources(
    tag: Optional[str] = None, featured: Optional[bool] = None, principal: Principal = Depends(current_principal),
) -> list[dict]:
    # A URN can embed a resource name, so filter before returning.
    candidates = await resource_tags.list_matching(deps.pool, tenant_id=principal.tenant_id, tag=tag, featured=featured)
    allowed = []
    for candidate in candidates:
        resource_type = _RESOURCE_AUTHZ_TYPE.get(parse_urn(candidate["resource_urn"]).type)
        if resource_type is None:
            continue
        decision = await deps.authz.authorize(
            principal, resource_type=resource_type, resource_urn=candidate["resource_urn"], permission="read",
        )
        if decision.allowed:
            allowed.append(candidate)
    return allowed


@router.post("/api/projects/{project_urn}/pins/{resource_urn}")
async def pin_resource(project_urn: str, resource_urn: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_project(principal, project_urn, "write")
    await project_pins.pin(
        deps.pool, tenant_id=principal.tenant_id, project_urn=project_urn, resource_urn=resource_urn,
        pinned_by_urn=principal.urn,
    )
    return {"status": "pinned", "project_urn": project_urn, "resource_urn": resource_urn}


@router.delete("/api/projects/{project_urn}/pins/{resource_urn}")
async def unpin_resource(project_urn: str, resource_urn: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_project(principal, project_urn, "write")
    await project_pins.unpin(
        deps.pool, tenant_id=principal.tenant_id, project_urn=project_urn, resource_urn=resource_urn,
    )
    return {"status": "unpinned", "project_urn": project_urn, "resource_urn": resource_urn}


@router.get("/api/projects/{project_urn}/pins")
async def list_project_pins(project_urn: str, principal: Principal = Depends(current_principal)) -> list[dict]:
    # Read, not write: a viewer still sees pins. Member URNs are filtered like collection members.
    await _authorize_project(principal, project_urn, "read")
    pins = await project_pins.list_pins(deps.pool, tenant_id=principal.tenant_id, project_urn=project_urn)
    readable = set(
        await _filter_readable_resource_urns(principal, [pin["resource_urn"] for pin in pins])
    )
    return [pin for pin in pins if pin["resource_urn"] in readable]
