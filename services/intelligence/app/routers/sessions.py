"""Agent sessions, turns, replay, and tool listing."""
from __future__ import annotations

from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from holon_common import HolonError, Principal
from holon_common.audit import emit_audit

from .. import agent_runtime, deps
from ..deps import (
    KNOWLEDGE_URL,
    _authorize_agent_session,
    _authorize_workspace,
    _seed_agent_session_authz,
    current_principal,
    enforce_spend,
    record_response_tokens,
    require_intelligence_enabled,
    require_own_session,
)

router = APIRouter()

class TurnRequest(BaseModel):
    message: str



class CreateSessionRequest(BaseModel):
    """Agent session creation request payload."""

    causation_id: Optional[str] = None
    causation_depth: int = 0
    chain_trigger: bool = False
    max_chain_depth: int = 10
    allowed_tools: Optional[list[str]] = None
    system_prompt: Optional[str] = None
    budget: Optional[dict] = None


@router.post("/sessions")
async def create_agent_session(
    request: CreateSessionRequest = CreateSessionRequest(), principal: Principal = Depends(current_principal)
) -> dict:
    """Create an agent runtime session.

    Read (not write): Agent App uses a viewer agent token; Knowledge write
    denial for that agent is a zero-tolerance security-suite check.
    """
    require_intelligence_enabled()
    await _authorize_workspace(principal, "read")
    if request.system_prompt and principal.type not in {"agent", "service_account"}:
        raise HolonError.forbidden(
            "PermissionDenied",
            "system_prompt is restricted to agent/service_account principals",
        )
    request.max_chain_depth = min(max(1, request.max_chain_depth), 10)
    await enforce_spend(principal)
    try:
        session = await agent_runtime.create_session(
            deps.pool,
            tenant_id=principal.tenant_id,
            agent_urn=principal.urn,
            on_behalf_of=principal.on_behalf_of,
            causation_id=request.causation_id,
            causation_depth=request.causation_depth,
            chain_trigger=request.chain_trigger,
            max_chain_depth=request.max_chain_depth,
            allowed_tools=request.allowed_tools,
            system_prompt=request.system_prompt,
            budget=request.budget,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('AgentRequestInvalid', str(exc)) from exc

    session_urn = session["urn"]

    async def _compensate():
        await deps.pool.execute("DELETE FROM agent_turn WHERE session_urn = $1", session_urn)
        await deps.pool.execute("DELETE FROM agent_session WHERE urn = $1", session_urn)

    await _seed_agent_session_authz(
        tenant_id=principal.tenant_id, session_urn=session_urn, compensate_delete=_compensate
    )
    emit_audit(
        category="access",
        action="intelligence.agent_session.created",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="agent_session",
        resource_urn=session_urn,
        extra={
            "chain_trigger": request.chain_trigger,
            "causation_depth": request.causation_depth,
            "on_behalf_of": principal.on_behalf_of,
        },
    )
    return session


@router.get("/tools")
async def list_available_tools(http_request: Request, principal: Principal = Depends(current_principal)) -> list[dict]:
    """List available agent tools and plugins."""
    require_intelligence_enabled()
    await _authorize_workspace(principal, "read")
    authorization = http_request.headers.get("authorization", "")
    async with httpx.AsyncClient(timeout=15.0) as http:
        return await agent_runtime.list_tools(deps.pool, http, KNOWLEDGE_URL, {"Authorization": authorization})


@router.get("/sessions/{session_urn:path}")
async def get_agent_session(session_urn: str, principal: Principal = Depends(current_principal)) -> dict:
    session = await agent_runtime.get_session(deps.pool, session_urn)
    require_own_session(session, principal)
    await _authorize_agent_session(principal, "read", session_urn=session_urn)
    return session


@router.post("/sessions/{session_urn:path}/turns")
async def run_agent_turn(
    session_urn: str, request: TurnRequest, http_request: Request, principal: Principal = Depends(current_principal)
) -> dict:
    require_intelligence_enabled()
    session = await agent_runtime.get_session(deps.pool, session_urn)
    require_own_session(session, principal)
    await _authorize_agent_session(principal, "read", session_urn=session_urn)
    await enforce_spend(principal)
    authorization = http_request.headers.get("authorization", "")
    try:
        body = await agent_runtime.run_turn(
            deps.pool,
            session_urn=session_urn,
            user_message=request.message,
            knowledge_url=KNOWLEDGE_URL,
            authorization=authorization,
            llm=deps.llm,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('AgentRequestInvalid', str(exc)) from exc
    await record_response_tokens(principal, body)
    return body


@router.post("/sessions/{session_urn:path}/replay")
async def replay_agent_session(session_urn: str, principal: Principal = Depends(current_principal)) -> dict:
    require_intelligence_enabled()
    session = await agent_runtime.get_session(deps.pool, session_urn)
    require_own_session(session, principal)
    await _authorize_agent_session(principal, "read", session_urn=session_urn)
    try:
        return await agent_runtime.replay_session(deps.pool, session_urn=session_urn, llm=deps.llm)
    except ValueError as exc:
        raise HolonError.invalid_argument('AgentRequestInvalid', str(exc)) from exc


