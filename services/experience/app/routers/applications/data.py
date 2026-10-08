"""Object-app list, detail, and declared actions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from holon_common import HolonError, Principal

from ... import application_builder
from ...deps import (
    KNOWLEDGE_URL,
    WORKSPACE_ID,
    _get_application_or_404,
    _get_json,
    _proxy,
    _upstream_authorization,
    _upstream_detail,
    current_principal,
)

router = APIRouter()

@router.get("/api/applications/{name}/data")
async def application_list_data(
    name: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Any:
    """Read list data for application objectApp surface."""
    application = await _get_application_or_404(name, principal)
    object_type = application_builder.resolve_object_app_object_type(application)
    if object_type is None:
        raise HolonError.invalid_argument('ApplicationSurfaceMissing', f"application {name!r} declares no objectApp surface")
    authorization = _upstream_authorization(http_request)
    object_set = application_builder.resolve_object_app_object_set(application)
    if object_set:
        status_code, body = await _get_json(
            f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objectSets/{object_set}/objects", authorization=authorization
        )
        if status_code != 200:
            raise HolonError.from_http(status_code, _upstream_detail(body), error_name="UpstreamError")
        if not isinstance(body, dict):
            raise HolonError.from_http(502, "unexpected object-set evaluate response", error_name='UpstreamBadResponse')
        if body.get("object_type") != object_type:
            raise HolonError.invalid_argument('ObjectSetEvaluateFailed', (
                    f"object set {object_set!r} targets {body.get('object_type')!r}, "
                    f"not application ObjectType {object_type!r}"),
            )
        return body.get("data", body.get("items", []))
    return await _proxy(
        "GET", f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objects/{object_type}", authorization=authorization
    )


@router.get("/api/applications/{name}/data/{instance_id}")
async def application_detail_data(
    name: str, instance_id: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Response:
    application = await _get_application_or_404(name, principal)
    object_type = application_builder.resolve_object_app_object_type(application)
    if object_type is None:
        raise HolonError.invalid_argument('ApplicationSurfaceMissing', f"application {name!r} declares no objectApp surface")
    return await _proxy(
        "GET",
        f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objects/{object_type}/{instance_id}",
        authorization=_upstream_authorization(http_request),
    )


@router.post("/api/applications/{name}/data/{instance_id}/actions/{action_name}")
async def application_invoke_action(
    name: str,
    instance_id: str,
    action_name: str,
    http_request: Request,
    principal: Principal = Depends(current_principal),
) -> Response:
    """Invoke action declared in application actionRefs."""
    application = await _get_application_or_404(name, principal)
    object_type = application_builder.resolve_object_app_object_type(application)
    if object_type is None:
        raise HolonError.invalid_argument('ApplicationSurfaceMissing', f"application {name!r} declares no objectApp surface")
    if not application_builder.is_action_declared(application, object_type, action_name):
        raise HolonError.forbidden(
            "ActionNotInApplication",
            f"application {name!r} did not declare {object_type}.{action_name}",
            application=name,
            object_type=object_type,
            action_name=action_name,
        )

    authorization = _upstream_authorization(http_request)
    full_name = f"{object_type}.{action_name}"
    body = await http_request.json()
    return await _proxy(
        "POST",
        f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objects/{object_type}/{instance_id}/actions/{full_name}",
        authorization=authorization,
        json=body,
    )
