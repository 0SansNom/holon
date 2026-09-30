from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from holon_common import HolonError, Principal
from holon_common.audit import emit_audit

from .. import application_builder, deps, ui_component_registry
from ..deps import (
    INTELLIGENCE_URL,
    KNOWLEDGE_URL,
    WORKSPACE_ID,
    WORKSPACE_URN,
    _agent_app_session_token,
    _application_not_found,
    _authorize_application,
    _get_application_or_404,
    _get_json,
    _json_response,
    _link_application_to_project,
    _post_json,
    _proxy,
    _upstream_authorization,
    _upstream_detail,
    current_principal,
)

router = APIRouter()


class ApplicationDefinitionRequest(BaseModel):
    definition: dict[str, Any]


class SetApplicationProjectRequest(BaseModel):
    project_urn: Optional[str] = None


@router.post("/api/applications/{name}")
async def create_or_update_application(
    name: str,
    body: ApplicationDefinitionRequest,
    http_request: Request,
    principal: Principal = Depends(current_principal),
) -> dict:
    # An unpromoted draft is edited in place. A promoted latest version starts a new draft.
    urn = application_builder.application_urn(principal.tenant_id, WORKSPACE_ID, name)
    existing = await application_builder.get_application(deps.pool, tenant_id=principal.tenant_id, name=name)
    if existing is not None:
        await _authorize_application(principal, urn, "write")
    else:
        decision = await deps.authz.authorize(
            principal, resource_type="workspace", resource_urn=WORKSPACE_URN, permission="write",
        )
        if not decision.allowed:
            raise HolonError.forbidden("PermissionDenied", decision.reason)

    authorization = _upstream_authorization(http_request) or ""
    try:
        result = await application_builder.create_or_update_draft(
            deps.pool,
            deps.client,
            tenant_id=principal.tenant_id,
            workspace_id=WORKSPACE_ID,
            name=name,
            definition=body.definition,
            knowledge_url=KNOWLEDGE_URL,
            intelligence_url=INTELLIGENCE_URL,
            authorization=authorization,
        )
    except application_builder.InvalidApplicationDefinition as exc:
        raise HolonError.invalid_argument("ExperienceValidationFailed", str(exc)) from exc

    if existing is None:
        await deps.authz.write_relationship(
            resource_type="application", resource_urn=urn, relation="parent_workspace",
            subject_type="workspace", subject_urn=WORKSPACE_URN,
        )
    emit_audit(
        category="access",
        action="experience.application.saved",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="application",
        resource_urn=urn,
        extra={"created": existing is None, "name": name},
    )
    return result


@router.get("/api/applications")
async def list_applications(principal: Principal = Depends(current_principal)) -> list[dict]:
    # Post-filtered: the application name itself is sensitive.
    applications = await application_builder.list_applications(deps.pool, tenant_id=principal.tenant_id)
    allowed = []
    for application in applications:
        decision = await deps.authz.authorize(
            principal, resource_type="application", resource_urn=application["urn"], permission="read",
        )
        if decision.allowed:
            allowed.append(application)
    return allowed


@router.get("/api/applications/{name}")
async def get_application(name: str, principal: Principal = Depends(current_principal)) -> dict:
    application = await application_builder.get_application(deps.pool, tenant_id=principal.tenant_id, name=name)
    if application is None:
        raise _application_not_found(name)
    await _authorize_application(principal, application["urn"], "read")
    return application


@router.post("/api/applications/{name}/project")
async def set_application_project(
    name: str, body: SetApplicationProjectRequest, principal: Principal = Depends(current_principal),
) -> dict:
    application = await application_builder.get_application(deps.pool, tenant_id=principal.tenant_id, name=name)
    if application is None:
        raise _application_not_found(name)
    # The application's write, not the target project's.
    await _authorize_application(principal, application["urn"], "write")
    await _link_application_to_project(application["urn"], body.project_urn)
    return await application_builder.set_application_project(
        deps.pool, tenant_id=principal.tenant_id, name=name, project_urn=body.project_urn,
    )


@router.post("/api/applications/{name}/promote")
async def promote_application(
    name: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> dict:
    await _get_application_or_404(name, principal, permission="write")
    authorization = _upstream_authorization(http_request) or ""
    try:
        result = await application_builder.promote(
            deps.pool,
            deps.client,
            tenant_id=principal.tenant_id,
            name=name,
            knowledge_url=KNOWLEDGE_URL,
            intelligence_url=INTELLIGENCE_URL,
            authorization=authorization,
        )
    except application_builder.InvalidApplicationDefinition as exc:
        raise HolonError.invalid_argument("ExperienceValidationFailed", str(exc)) from exc
    emit_audit(
        category="access",
        action="experience.application.promoted",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="application",
        resource_urn=application_builder.application_urn(principal.tenant_id, WORKSPACE_ID, name),
        extra={"name": name},
    )
    return result


@router.get("/api/applications/{name}/data")
async def application_list_data(
    name: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Any:
    application = await _get_application_or_404(name, principal)
    object_type = application_builder.resolve_object_app_object_type(application)
    if object_type is None:
        raise HolonError.invalid_argument(
            "ApplicationSurfaceMissing", f"application {name!r} declares no objectApp surface"
        )
    authorization = _upstream_authorization(http_request)
    object_set = application_builder.resolve_object_app_object_set(application)
    if object_set:
        status_code, body = await _get_json(
            f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objectSets/{object_set}/objects", authorization=authorization
        )
        if status_code != 200:
            raise HolonError.from_http(status_code, _upstream_detail(body), error_name="UpstreamError")
        if not isinstance(body, dict):
            raise HolonError.from_http(502, "unexpected object-set evaluate response", error_name="UpstreamBadResponse")
        if body.get("object_type") != object_type:
            raise HolonError.invalid_argument(
                "ObjectSetEvaluateFailed",
                (
                    f"object set {object_set!r} targets {body.get('object_type')!r}, "
                    f"not application ObjectType {object_type!r}"
                ),
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
        raise HolonError.invalid_argument(
            "ApplicationSurfaceMissing", f"application {name!r} declares no objectApp surface"
        )
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
    application = await _get_application_or_404(name, principal)
    object_type = application_builder.resolve_object_app_object_type(application)
    if object_type is None:
        raise HolonError.invalid_argument(
            "ApplicationSurfaceMissing", f"application {name!r} declares no objectApp surface"
        )
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


@router.get("/api/applications/{name}/dashboard")
async def application_dashboard(
    name: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> dict:
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
                raise HolonError.from_http(502, "unexpected object-set evaluate response", error_name="UpstreamBadResponse")
            declared_type = widget.get("objectType")
            if declared_type and body.get("object_type") != declared_type:
                raise HolonError.invalid_argument(
                    "ObjectSetEvaluateFailed",
                    (
                        f"object set {object_set!r} targets {body.get('object_type')!r}, "
                        f"not widget ObjectType {declared_type!r}"
                    ),
                )
            rows = body.get("data", []) if isinstance(body.get("data"), list) else (
                body.get("items", []) if isinstance(body.get("items"), list) else []
            )
        else:
            status_code, body = await _get_json(
                f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objects/{widget['objectType']}", authorization=authorization
            )
            if status_code != 200:
                # Pass the upstream string, not the whole body: `detail=body` nests `{"detail": {"detail": ...}}`.
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


@router.post("/api/applications/{name}/analytics/execute")
async def application_analytics_execute(
    name: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Response:
    application = await _get_application_or_404(name, principal)
    object_type = application_builder.resolve_analytics_object_type(application)
    if object_type is None:
        raise HolonError.invalid_argument(
            "ApplicationSurfaceMissing", f"application {name!r} declares no analytics surface"
        )

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
    application = await _get_application_or_404(name, principal)
    if application_builder.resolve_analytics_object_type(application) is None:
        raise HolonError.invalid_argument(
            "ApplicationSurfaceMissing", f"application {name!r} declares no analytics surface"
        )
    return await _proxy(
        "POST",
        f"{KNOWLEDGE_URL}/api/holon/execute/{plan_hash}/replay",
        authorization=_upstream_authorization(http_request),
    )


@router.get("/api/applications/{name}/form")
async def get_application_form(name: str, principal: Principal = Depends(current_principal)) -> dict:
    application = await _get_application_or_404(name, principal)
    form = application_builder.get_form_surface(application)
    if form is None:
        raise HolonError.invalid_argument(
            "ApplicationSurfaceMissing", f"application {name!r} declares no form surface"
        )
    return {"action": form["action"], "fields": form["fields"]}


@router.post("/api/applications/{name}/form/{instance_id}")
async def submit_application_form(
    name: str, instance_id: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Response:
    application = await _get_application_or_404(name, principal)
    form = application_builder.get_form_surface(application)
    if form is None:
        raise HolonError.invalid_argument(
            "ApplicationSurfaceMissing", f"application {name!r} declares no form surface"
        )

    submitted = await http_request.json()
    try:
        application_builder.validate_form_submission(form, submitted)
    except application_builder.FormValidationError as exc:
        raise HolonError.invalid_argument("ExperienceValidationFailed", str(exc)) from exc

    object_type, local_action_name = form["action"].split(".", 1)
    return await _proxy(
        "POST",
        f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objects/{object_type}/{instance_id}/actions/{local_action_name}",
        authorization=_upstream_authorization(http_request),
        json=submitted,
    )


@router.post("/api/applications/{name}/agent-sessions")
async def create_application_agent_session(name: str, principal: Principal = Depends(current_principal)) -> Response:
    application = await _get_application_or_404(name, principal)
    agent_app = application_builder.resolve_agent_app_config(application)
    if agent_app is None:
        raise HolonError.invalid_argument(
            "ApplicationSurfaceMissing", f"application {name!r} declares no agentApp surface"
        )

    token = _agent_app_session_token(principal.urn)
    status_code, body = await _post_json(
        f"{INTELLIGENCE_URL}/sessions",
        authorization=f"Bearer {token}",
        json={
            "allowed_tools": agent_app.get("tools"),
            "system_prompt": agent_app.get("systemPrompt"),
            "budget": agent_app.get("budget"),
        },
    )
    if status_code == 200:
        await application_builder.record_agent_app_session(
            deps.pool,
            session_urn=body["urn"],
            tenant_id=principal.tenant_id,
            application_name=name,
            created_by_urn=principal.urn,
        )
    return _json_response(body, status_code)


@router.post("/api/applications/{name}/agent-sessions/{session_urn:path}/turns")
async def run_application_agent_session_turn(
    name: str, session_urn: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Response:
    await _get_application_or_404(name, principal)
    owner_urn = await application_builder.get_agent_app_session_owner(deps.pool, session_urn)
    if owner_urn is None or owner_urn != principal.urn:
        raise HolonError.not_found(
            "AgentSessionNotFound", f"no agent session {session_urn!r} found for this application"
        )

    token = _agent_app_session_token(principal.urn)
    body = await http_request.json()
    return await _proxy(
        "POST", f"{INTELLIGENCE_URL}/sessions/{session_urn}/turns", authorization=f"Bearer {token}", json=body
    )
