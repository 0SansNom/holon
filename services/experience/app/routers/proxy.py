from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request, Response

from holon_common import Principal, is_production
from holon_common.audit_store import list_events_page

from .. import deps
from ..deps import (
    CONNECTIVITY_URL,
    IDENTITY_URL,
    INTELLIGENCE_URL,
    KNOWLEDGE_URL,
    TENANT_ID,
    WORKSPACE_ID,
    _authorize_workspace,
    _intelligence_enabled,
    _proxy,
    _relay,
    _upstream_authorization,
    current_principal,
)

router = APIRouter()


@router.api_route("/api/identity/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_identity(path: str, request: Request) -> Response:
    return await _relay(IDENTITY_URL, path, request)


@router.api_route("/api/connectivity/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_connectivity(
    path: str, request: Request, _: Principal = Depends(current_principal)
) -> Response:
    return await _relay(CONNECTIVITY_URL, path, request)


@router.api_route("/api/knowledge/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_knowledge(
    path: str, request: Request, _: Principal = Depends(current_principal)
) -> Response:
    return await _relay(KNOWLEDGE_URL, path, request)


@router.api_route("/api/intelligence/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_intelligence(
    path: str, request: Request, _: Principal = Depends(current_principal)
) -> Response:
    return await _relay(INTELLIGENCE_URL, path, request)


@router.get("/api/config")
async def config() -> dict:
    """No demo principal or ObjectType: an empty instance is valid."""
    return {
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "intelligence_enabled": _intelligence_enabled(),
        "require_connector_secret_ref": is_production(),
    }


@router.get("/api/audit-events")
async def list_experience_audit_events(
    principal: Principal = Depends(current_principal),
    category: Optional[str] = None,
    action: Optional[str] = None,
    actor: Optional[str] = None,
    outcome: Optional[str] = None,
    pageSize: Optional[int] = None,
    pageToken: Optional[str] = None,
) -> dict:
    await _authorize_workspace(principal, "approve")
    return await list_events_page(
        deps.pool,
        principal.tenant_id,
        category=category,
        action=action,
        actor_urn=actor,
        outcome=outcome,
        page_size=50 if pageSize is None else pageSize,
        page_token=pageToken,
    )


@router.get("/api/lineage/{urn:path}")
async def get_lineage(
    urn: str, request: Request, _: Principal = Depends(current_principal)
) -> Response:
    query = str(request.url.query)
    target = f"{KNOWLEDGE_URL}/api/holon/lineage/{urn}"
    if query:
        target = f"{target}?{query}"
    return await _proxy(
        "GET",
        target,
        authorization=_upstream_authorization(request),
    )
