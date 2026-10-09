"""Analytics execute and replay for an application surface."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from holon_common import HolonError, Principal

from ... import application_builder
from ...deps import (
    KNOWLEDGE_URL,
    _get_application_or_404,
    _proxy,
    _upstream_authorization,
    current_principal,
)

router = APIRouter()

@router.post("/api/applications/{name}/analytics/execute")
async def application_analytics_execute(
    name: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Response:
    """Execute ad-hoc analytics query for application analytics surface."""
    application = await _get_application_or_404(name, principal)
    object_type = application_builder.resolve_analytics_object_type(application)
    if object_type is None:
        raise HolonError.invalid_argument('ApplicationSurfaceMissing', f"application {name!r} declares no analytics surface")

    body = await http_request.json()
    if body.get("object_type") != object_type:
        raise HolonError.forbidden(
            "AnalyticsObjectTypeMismatch",
            f"application {name!r}'s analytics surface is scoped to {object_type!r}, not {body.get('object_type')!r}",
            application=name,
            expected_object_type=object_type,
            got_object_type=body.get("object_type"),
        )
    return await _proxy(
        "POST", f"{KNOWLEDGE_URL}/api/holon/execute", authorization=_upstream_authorization(http_request), json=body
    )


@router.post("/api/applications/{name}/analytics/{plan_hash}/replay")
async def application_analytics_replay(
    name: str, plan_hash: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Response:
    """Replay execution plan for application analytics surface."""
    application = await _get_application_or_404(name, principal)
    if application_builder.resolve_analytics_object_type(application) is None:
        raise HolonError.invalid_argument('ApplicationSurfaceMissing', f"application {name!r} declares no analytics surface")
    return await _proxy(
        "POST",
        f"{KNOWLEDGE_URL}/api/holon/execute/{plan_hash}/replay",
        authorization=_upstream_authorization(http_request),
    )
