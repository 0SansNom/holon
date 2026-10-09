"""Read-only dashboard widgets for an application."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from holon_common import HolonError, Principal

from ... import application_builder, deps, ui_component_registry
from ...deps import (
    KNOWLEDGE_URL,
    WORKSPACE_ID,
    _get_application_or_404,
    _get_json,
    _upstream_authorization,
    _upstream_detail,
    current_principal,
)

router = APIRouter()

@router.get("/api/applications/{name}/dashboard")
async def application_dashboard(
    name: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> dict:
    """Fetch read-only widget data for application dashboard surface."""
    application = await _get_application_or_404(name, principal)
    authorization = _upstream_authorization(http_request)
    widgets_out = []
    for widget in application_builder.get_dashboard_widgets(application):
        object_set = widget.get("objectSet")
        if object_set:
            status_code, body = await _get_json(
                f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objectSets/{object_set}/objects", authorization=authorization
            )
            if status_code != 200:
                raise HolonError.from_http(status_code, _upstream_detail(body), error_name="UpstreamError")
            if not isinstance(body, dict):
                raise HolonError.from_http(502, "unexpected object-set evaluate response", error_name='UpstreamBadResponse')
            declared_type = widget.get("objectType")
            if declared_type and body.get("object_type") != declared_type:
                raise HolonError.invalid_argument('ObjectSetEvaluateFailed', (
                        f"object set {object_set!r} targets {body.get('object_type')!r}, "
                        f"not widget ObjectType {declared_type!r}"),
                )
            rows = body.get("data", []) if isinstance(body.get("data"), list) else (
                body.get("items", []) if isinstance(body.get("items"), list) else []
            )
        else:
            status_code, body = await _get_json(
                f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objects/{widget['objectType']}", authorization=authorization
            )
            if status_code != 200:
                # `body` is whatever `_get_json` got back from upstream — usually
                # already a flat `{"detail": "..."}`, but proxying it verbatim as
                # `detail=body` would nest it (`{"detail": {"detail": "..."}}`)
                # instead of matching every other error response's flat shape.
                raise HolonError.from_http(status_code, _upstream_detail(body), error_name="UpstreamError")
            if isinstance(body, dict):
                rows = body.get("data") if isinstance(body.get("data"), list) else (
                    body.get("items") if isinstance(body.get("items"), list) else []
                )
            else:
                rows = body if isinstance(body, list) else []
        if widget["component"] == "kpi":
            widgets_out.append(
                {
                    "label": widget.get("label"),
                    "component": "kpi",
                    "value": len(rows),
                    "objectSet": object_set,
                }
            )
        elif widget["component"] == "table":
            widgets_out.append(
                {
                    "label": widget.get("label"),
                    "component": "table",
                    "rows": rows,
                    "objectSet": object_set,
                }
            )
        else:
            plugin_registration = await ui_component_registry.get_component_registration_by_name(
                deps.pool, widget["component"]
            )
            widgets_out.append(
                {
                    "label": widget.get("label"),
                    "component": widget["component"],
                    "rows": rows,
                    "objectSet": object_set,
                    "iframeUrl": plugin_registration["manifest"]["iframe_url"] if plugin_registration else None,
                }
            )
    return {"applicationName": name, "widgets": widgets_out}
