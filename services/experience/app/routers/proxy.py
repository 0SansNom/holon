"""Relays to Identity, Connectivity, Knowledge, and Intelligence."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from holon_common import Principal

from ..deps import (
    CONNECTIVITY_URL,
    IDENTITY_URL,
    INTELLIGENCE_URL,
    KNOWLEDGE_URL,
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
