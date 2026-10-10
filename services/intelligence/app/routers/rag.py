"""Ask / RAG, semantic index rebuild, and evaluation suites."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from holon_common import HolonError, Principal

from .. import deps, evaluation, vector_store
from ..context_builder import ask as context_builder_ask
from ..deps import (
    KNOWLEDGE_URL,
    TENANT_ID,
    WORKSPACE_ID,
    _authorize_workspace,
    current_principal,
    enforce_spend,
    indexer_token,
    record_response_tokens,
    require_intelligence_enabled,
    security_probe_tokens,
)
from ..knowledge_urls import holon_url

router = APIRouter()


class AskRequest(BaseModel):
    query: str


@router.post("/semantic-index/rebuild")
async def rebuild_semantic_index(principal: Principal = Depends(current_principal)) -> dict:
    """Re-index ontology metadata + glossary into Qdrant (tenant-scoped).

    Boot indexes before CI fixtures exist; provision calls this after seeding.
    """
    require_intelligence_enabled()
    await _authorize_workspace(principal, "write")
    indexed = await vector_store.index_metadata(
        deps.qdrant,
        deps.embedder,
        knowledge_url=KNOWLEDGE_URL,
        token=indexer_token(),
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
    )
    return {"indexed": indexed, "tenant_id": TENANT_ID}


@router.post("/ask")
async def ask(request: AskRequest, http_request: Request, principal: Principal = Depends(current_principal)) -> dict:
    """RAG ask endpoint using permission-filtered context."""
    require_intelligence_enabled()
    await _authorize_workspace(principal, "read")
    await enforce_spend(principal)
    authorization = http_request.headers.get("authorization", "")
    async with httpx.AsyncClient(timeout=15.0) as http:
        response = await http.get(holon_url(KNOWLEDGE_URL, "/glossary"), headers={"Authorization": authorization})
        response.raise_for_status()
        glossary_terms = response.json()

    try:
        body = await context_builder_ask(
            query_text=request.query,
            authorization=authorization,
            knowledge_url=KNOWLEDGE_URL,
            qdrant=deps.qdrant,
            embedder=deps.embedder,
            glossary_terms=glossary_terms,
            llm=deps.llm,
            tenant_id=principal.tenant_id,
        )
    except httpx.HTTPStatusError as exc:
        raise HolonError.from_http(exc.response.status_code, exc.response.text, error_name='UpstreamError') from exc
    await record_response_tokens(principal, body)
    return body



@router.post("/evaluate")
async def evaluate(http_request: Request, principal: Principal = Depends(current_principal)) -> dict:
    """Run gold set, security suite, and action-path (criteria) suite."""
    require_intelligence_enabled()
    await _authorize_workspace(principal, "write")
    await enforce_spend(principal)
    authorization = http_request.headers.get("authorization", "")
    async with httpx.AsyncClient(timeout=15.0) as http:
        response = await http.get(holon_url(KNOWLEDGE_URL, "/glossary"), headers={"Authorization": authorization})
        response.raise_for_status()
        glossary_terms = response.json()

    gold_set_result = await evaluation.run_gold_set(
        deps.pool,
        authorization=authorization,
        knowledge_url=KNOWLEDGE_URL,
        qdrant=deps.qdrant,
        embedder=deps.embedder,
        glossary_terms=glossary_terms,
        llm=deps.llm,
        tenant_id=principal.tenant_id,
    )
    agent_token, editor_token = security_probe_tokens()
    security_result = await evaluation.run_security_suite(
        knowledge_url=KNOWLEDGE_URL, agent_token=agent_token, editor_token=editor_token
    )
    path_result = await evaluation.run_action_path_suite(
        knowledge_url=KNOWLEDGE_URL, editor_token=editor_token
    )
    return {"goldSet": gold_set_result, "security": security_result, "actionPaths": path_result}


