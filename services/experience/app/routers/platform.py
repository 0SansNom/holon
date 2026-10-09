"""Public config, audit trail, and lineage proxy."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, Request, Response

from holon_common import Principal, is_production
from holon_common.audit_store import list_events
from holon_common.service_runtime import list_audit_events_http

from .. import deps
from ..deps import (
    AUTOMATION_URL,
    CONNECTIVITY_URL,
    IDENTITY_URL,
    INTELLIGENCE_URL,
    KNOWLEDGE_URL,
    TENANT_ID,
    WORKSPACE_ID,
    _authorize_workspace,
    _get_json,
    _intelligence_enabled,
    _proxy,
    _upstream_authorization,
    current_principal,
)

router = APIRouter()

_AUDIT_TRACE_PAGE_SIZE = 100


def _audit_sources() -> list[tuple[str, str]]:
    sources = [
        ("identity", f"{IDENTITY_URL}/audit-events"),
        ("connectivity", f"{CONNECTIVITY_URL}/audit-events"),
        ("knowledge", f"{KNOWLEDGE_URL}/api/holon/audit-events"),
        ("intelligence", f"{INTELLIGENCE_URL}/audit-events"),
    ]
    if AUTOMATION_URL:
        sources.append(("automation", f"{AUTOMATION_URL}/audit-events"))
    return sources


@router.get("/api/config")
async def config() -> dict:
    """Public bootstrap flags. No demo principal or ObjectType — the
    instance may be empty (ADR 026) and login is Identity's job.
    """
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
    traceId: Optional[str] = None,
    pageSize: Optional[int] = None,
    pageToken: Optional[str] = None,
) -> dict:
    """Durable Experience audit (applications, collections, UI plugins)."""
    await _authorize_workspace(principal, "approve")
    return await list_audit_events_http(
        deps.pool,
        principal.tenant_id,
        category=category,
        action=action,
        actor=actor,
        outcome=outcome,
        traceId=traceId,
        pageSize=pageSize,
        pageToken=pageToken,
    )


@router.get("/api/audit-events/trace/{trace_id}")
async def get_audit_trace(
    trace_id: str, request: Request, principal: Principal = Depends(current_principal)
) -> dict:
    """Every service's audit records for one action (one correlation id), oldest first.

    Each service still enforces its own audit permission. A service that
    cannot answer is listed in `unavailable` instead of failing the view;
    one with more than a page of records is listed in `truncated`.
    """
    await _authorize_workspace(principal, "approve")
    authorization = _upstream_authorization(request)
    query = httpx.QueryParams({"traceId": trace_id, "pageSize": _AUDIT_TRACE_PAGE_SIZE})

    async def fetch(service: str, url: str) -> tuple[str, int, Any]:
        try:
            status, body = await _get_json(f"{url}?{query}", authorization=authorization)
        except httpx.HTTPError as exc:
            return service, 503, {"detail": str(exc)}
        return service, status, body

    local = await list_events(
        deps.pool, principal.tenant_id, trace_id=trace_id, page_size=_AUDIT_TRACE_PAGE_SIZE + 1
    )
    events = [{**event, "service": "experience"} for event in local[:_AUDIT_TRACE_PAGE_SIZE]]
    truncated = ["experience"] if len(local) > _AUDIT_TRACE_PAGE_SIZE else []
    unavailable: list[dict] = []
    for service, status, body in await asyncio.gather(*(fetch(name, url) for name, url in _audit_sources())):
        if status != 200 or not isinstance(body, dict):
            unavailable.append({"service": service, "status": status})
            continue
        events.extend({**event, "service": service} for event in body.get("data") or [])
        if body.get("nextPageToken"):
            truncated.append(service)
    events.sort(key=lambda event: event.get("occurredAt") or "")
    return {"traceId": trace_id, "data": events, "unavailable": unavailable, "truncated": truncated}


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
