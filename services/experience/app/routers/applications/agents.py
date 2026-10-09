"""Agent App sessions and turns."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request, Response

from holon_common import HolonError, Principal

from ... import application_builder, deps
from ...deps import (
    INTELLIGENCE_URL,
    _agent_app_session_token,
    _get_application_or_404,
    _post_json,
    _proxy,
    current_principal,
)

router = APIRouter()

@router.post("/api/applications/{name}/agent-sessions")
async def create_application_agent_session(name: str, principal: Principal = Depends(current_principal)) -> Response:
    """Create agent session for application agentApp surface."""
    application = await _get_application_or_404(name, principal)
    agent_app = application_builder.resolve_agent_app_config(application)
    if agent_app is None:
        raise HolonError.invalid_argument('ApplicationSurfaceMissing', f"application {name!r} declares no agentApp surface")

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
    return Response(content=json.dumps(body).encode(), status_code=status_code, media_type="application/json")


@router.post("/api/applications/{name}/agent-sessions/{session_urn:path}/turns")
async def run_application_agent_session_turn(
    name: str, session_urn: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Response:
    """Execute turn for application agentApp session."""
    await _get_application_or_404(name, principal)
    owner_urn = await application_builder.get_agent_app_session_owner(deps.pool, session_urn)
    if owner_urn is None or owner_urn != principal.urn:
        raise HolonError.not_found('AgentSessionNotFound', f"no agent session {session_urn!r} found for this application")

    token = _agent_app_session_token(principal.urn)
    body = await http_request.json()
    return await _proxy(
        "POST", f"{INTELLIGENCE_URL}/sessions/{session_urn}/turns", authorization=f"Bearer {token}", json=body
    )
