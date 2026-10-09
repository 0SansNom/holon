"""Form schema and submission for an application surface."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from holon_common import HolonError, Principal

from ... import application_builder
from ...deps import (
    KNOWLEDGE_URL,
    WORKSPACE_ID,
    _get_application_or_404,
    _proxy,
    _upstream_authorization,
    current_principal,
)

router = APIRouter()

@router.get("/api/applications/{name}/form")
async def get_application_form(name: str, principal: Principal = Depends(current_principal)) -> dict:
    """The **form** surface — returns the declared field schema.
    """
    application = await _get_application_or_404(name, principal)
    form = application_builder.get_form_surface(application)
    if form is None:
        raise HolonError.invalid_argument('ApplicationSurfaceMissing', f"application {name!r} declares no form surface")
    return {"action": form["action"], "fields": form["fields"]}


@router.post("/api/applications/{name}/form/{instance_id}")
async def submit_application_form(
    name: str, instance_id: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Response:
    """Validates the submission against the form's declared schema (required/type).
    """
    application = await _get_application_or_404(name, principal)
    form = application_builder.get_form_surface(application)
    if form is None:
        raise HolonError.invalid_argument('ApplicationSurfaceMissing', f"application {name!r} declares no form surface")

    submitted = await http_request.json()
    try:
        application_builder.validate_form_submission(form, submitted)
    except application_builder.FormValidationError as exc:
        raise HolonError.invalid_argument('ExperienceValidationFailed', str(exc)) from exc

    object_type, local_action_name = form["action"].split(".", 1)
    return await _proxy(
        "POST",
        f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objects/{object_type}/{instance_id}/actions/{local_action_name}",
        authorization=_upstream_authorization(http_request),
        json=submitted,
    )
