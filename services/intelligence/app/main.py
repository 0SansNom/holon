"""Intelligence Platform — LLM Gateway, Context Builder, Agent Runtime, Evaluation."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import base64

import boto3
import httpx
from botocore.config import Config as BotoConfig
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel
from qdrant_client import AsyncQdrantClient

from holon_common import (
    EventProducer,
    Principal,
    active_jwt,
    assert_production_posture,
    build_urn,
    configure_json_logging,
    create_pool,
    install_error_handlers,
    instrument_cors,
    instrument_metrics,
    instrument_tracing,
    is_production,
    issue_token,
    make_principal_dependency,
    outbox,
    retry_with_backoff,
    run_migrations,
)
from holon_common.authz import PermissionClient

from . import agent_runtime, evaluation, model_registry, spend_limits, tool_plugin_registry, vector_store
from .context_builder import ask as context_builder_ask
from .embeddings import build_embedding_client
from .llm_gateway import build_llm_client
from .spend_limits import SpendLimitExceeded

SERVICE_NAME = "intelligence-platform"
configure_json_logging(SERVICE_NAME)

TENANT_ID = os.environ["HOLON_TENANT_ID"]
WORKSPACE_ID = os.environ["HOLON_WORKSPACE_ID"]
JWT_SECRET, JWT_ACTIVE_KID, JWT_SECRETS = active_jwt()
DB_URL = os.environ["HOLON_DB_URL"]
KAFKA_BOOTSTRAP = os.environ["HOLON_KAFKA_BOOTSTRAP"]
KNOWLEDGE_URL = os.environ["HOLON_KNOWLEDGE_URL"]
QDRANT_URL = os.environ["HOLON_QDRANT_URL"]
OTLP_ENDPOINT = os.environ.get("HOLON_OTLP_ENDPOINT", "")
SPICEDB_URL = os.environ["HOLON_SPICEDB_URL"]
SPICEDB_PRESHARED_KEY = os.environ["HOLON_SPICEDB_PRESHARED_KEY"]
OPA_URL = os.environ["HOLON_OPA_URL"]

S3_ENDPOINT = os.environ["HOLON_S3_ENDPOINT"]
AWS_ACCESS_KEY_ID = os.environ["AWS_ACCESS_KEY_ID"]
AWS_SECRET_ACCESS_KEY = os.environ["AWS_SECRET_ACCESS_KEY"]
AWS_REGION = os.environ["AWS_REGION"]
MODEL_BUCKET = "holon-warehouse"

INDEXER_URN = build_urn(TENANT_ID, "global", "service-account", "intelligence-indexer")
AGENT_URN = build_urn(TENANT_ID, "global", "agent", "ingest-bot")
JDOE_URN = build_urn(TENANT_ID, "global", "user", "jdoe")
WORKSPACE_URN = build_urn(TENANT_ID, "global", "workspace", WORKSPACE_ID)
logger = logging.getLogger("intelligence")


def _intelligence_enabled() -> bool:
    # Default on for local; production posture requires explicit false.
    return os.environ.get("HOLON_INTELLIGENCE_ENABLED", "true").lower() in {"1", "true", "yes"}


def _indexer_token() -> str:
    principal = Principal(
        urn=INDEXER_URN, type="service_account", tenant_id=TENANT_ID, display_name="Intelligence Semantic Indexer"
    )
    return issue_token(
        principal, JWT_SECRET, ttl_seconds=300, kid=JWT_ACTIVE_KID, secrets=JWT_SECRETS
    )


def _security_probe_tokens() -> tuple[str, str]:
    """Mint security probe tokens for internal evaluation.

    Production never mints user JWTs here (Identity-only). Skip the editor
    user probe and reuse the agent token so callers stay signature-compatible.
    """
    agent = Principal(
        urn=AGENT_URN, type="agent", tenant_id=TENANT_ID, display_name="Ingest Bot", on_behalf_of=JDOE_URN, country="FR"
    )
    agent_token = issue_token(agent, JWT_SECRET, ttl_seconds=60, kid=JWT_ACTIVE_KID, secrets=JWT_SECRETS)
    if is_production():
        return agent_token, agent_token
    editor = Principal(urn=JDOE_URN, type="user", tenant_id=TENANT_ID, display_name="Jane Doe", country="FR")
    return (
        agent_token,
        issue_token(editor, JWT_SECRET, ttl_seconds=60, kid=JWT_ACTIVE_KID, secrets=JWT_SECRETS, allow_user=True),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_production_posture(service_name=SERVICE_NAME)
    app.state.intelligence_enabled = _intelligence_enabled()
    app.state.pool = await create_pool(DB_URL)
    async with app.state.pool.acquire() as conn:
        await agent_runtime.ensure_schema(conn)
        await evaluation.ensure_schema(conn)
        await tool_plugin_registry.ensure_schema(conn)
        await model_registry.ensure_schema(conn)
        await spend_limits.ensure_schema(conn)
        await outbox.ensure_schema(conn)
    await run_migrations(app.state.pool, Path(__file__).parent / "migrations")

    app.state.authz = PermissionClient(SPICEDB_URL, SPICEDB_PRESHARED_KEY, OPA_URL)

    app.state.s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_REGION,
        config=BotoConfig(
            s3={"addressing_style": "path"},
            connect_timeout=3,
            read_timeout=5,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )

    app.state.producer = EventProducer(KAFKA_BOOTSTRAP)
    await app.state.producer.start()
    relay_task = asyncio.create_task(
        outbox.relay_forever(app.state.pool, app.state.producer, dlq_producer=app.state.producer)
    )

    app.state.embedder = build_embedding_client()
    # When the product flag is off, force fake LLM so no accidental spend.
    if not app.state.intelligence_enabled:
        os.environ.setdefault("HOLON_LLM_PROVIDER", "fake")
    app.state.llm = build_llm_client()

    app.state.qdrant = AsyncQdrantClient(url=QDRANT_URL, check_compatibility=False, timeout=10)
    await retry_with_backoff(
        lambda: vector_store.ensure_collection(app.state.qdrant, app.state.embedder.dimension),
        what="qdrant collection setup",
    )
    indexed = await retry_with_backoff(
        lambda: vector_store.index_metadata(
            app.state.qdrant, app.state.embedder, knowledge_url=KNOWLEDGE_URL, token=_indexer_token()
        ),
        what="semantic index build",
    )
    logger.info(
        "indexed %d metadata documents into Qdrant (intelligence_enabled=%s)",
        indexed,
        app.state.intelligence_enabled,
    )

    sweep_task = asyncio.create_task(agent_runtime.sweep_expired_sessions_forever(app.state.pool))

    yield

    sweep_task.cancel()
    relay_task.cancel()
    await app.state.producer.stop()
    await app.state.authz.aclose()
    await app.state.qdrant.close()
    await app.state.pool.close()


app = FastAPI(title="Holon — Intelligence Platform", lifespan=lifespan)
instrument_cors(app)
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
install_error_handlers(app, service_name=SERVICE_NAME)
current_principal = make_principal_dependency(JWT_SECRET, secrets=JWT_SECRETS)



def _require_intelligence_enabled() -> None:
    if not getattr(app.state, "intelligence_enabled", True):
        raise HTTPException(
            status_code=503,
            detail="Intelligence is disabled (HOLON_INTELLIGENCE_ENABLED=false)",
        )


async def _authorize_workspace(principal: Principal, permission: str) -> None:
    decision = await app.state.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=build_urn(principal.tenant_id, "global", "workspace", WORKSPACE_ID),
        permission=permission,
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)


async def _enforce_spend(principal: Principal) -> None:
    try:
        await spend_limits.enforce_before_spend(
            app.state.pool, tenant_id=principal.tenant_id, principal_urn=principal.urn
        )
    except SpendLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=exc.detail) from exc


async def _record_response_tokens(principal: Principal, body: dict) -> None:
    tokens = body.get("tokens") or {}
    total = int(tokens.get("input") or 0) + int(tokens.get("output") or 0)
    consumed = body.get("consumed") or {}
    if "tokens" in consumed:
        total = max(total, int(consumed.get("tokens") or 0))
    await spend_limits.record_tokens(app.state.pool, tenant_id=principal.tenant_id, tokens=total)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/live")
async def live() -> dict:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict:
    await app.state.pool.fetchval("SELECT 1")
    return {"status": "ok", "intelligence_enabled": bool(getattr(app.state, "intelligence_enabled", True))}


class AskRequest(BaseModel):
    query: str


@app.post("/ask")
async def ask(request: AskRequest, http_request: Request, principal: Principal = Depends(current_principal)) -> dict:
    """RAG ask — workspace read + product flag. Knowledge PDP still applies
    on forwarded structural/lexical channels.
    """
    _require_intelligence_enabled()
    await _authorize_workspace(principal, "read")
    await _enforce_spend(principal)
    authorization = http_request.headers.get("authorization", "")
    async with httpx.AsyncClient(timeout=15.0) as http:
        response = await http.get(f"{KNOWLEDGE_URL}/glossary", headers={"Authorization": authorization})
        response.raise_for_status()
        glossary_terms = response.json()

    try:
        body = await context_builder_ask(
            query_text=request.query,
            authorization=authorization,
            knowledge_url=KNOWLEDGE_URL,
            qdrant=app.state.qdrant,
            embedder=app.state.embedder,
            glossary_terms=glossary_terms,
            llm=app.state.llm,
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    await _record_response_tokens(principal, body)
    return body


class TurnRequest(BaseModel):
    message: str


def _require_own_session(session: dict | None, principal: Principal) -> dict:
    if session is None or session["agent_urn"] != principal.urn:
        raise HTTPException(status_code=404, detail="no agent_session found for that urn")
    return session


class CreateSessionRequest(BaseModel):
    """Loop-detection fields — normally absent (defaults make a
    plain `POST /sessions` with no body behave exactly as before). Set by
    `services/automation/app/agent_chain_trigger.py` when a session is
    spawned in response to an event, threading the causal chain forward.

    `allowed_tools`/`system_prompt`/`budget`: set by Experience
    when compiling an `agentApp` surface into a session — absent (the
    default) behaves exactly as before, same as the loop-detection fields.
    """

    causation_id: Optional[str] = None
    causation_depth: int = 0
    chain_trigger: bool = False
    max_chain_depth: int = 10
    allowed_tools: Optional[list[str]] = None
    system_prompt: Optional[str] = None
    budget: Optional[dict] = None


@app.post("/sessions")
async def create_agent_session(
    request: CreateSessionRequest = CreateSessionRequest(), principal: Principal = Depends(current_principal)
) -> dict:
    """The caller *is* the agent (its own JWT already carries
    `on_behalf_of`, set when the agent principal was provisioned via
    Identity's self-serve `insert_principal`); a session can't be opened
    declaring an arbitrary mandant in the request body.
    """
    _require_intelligence_enabled()
    await _authorize_workspace(principal, "read")
    await _enforce_spend(principal)
    try:
        return await agent_runtime.create_session(
            app.state.pool,
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/tools")
async def list_available_tools(http_request: Request, principal: Principal = Depends(current_principal)) -> list[dict]:
    """the live tool-name catalog `application_builder.py`
    validates an `agentApp` surface's declared allowlist against —
    exactly the same computation `_list_tools` does internally on every
    turn, exposed read-only so a caller can see what's available before
    (or without) opening a session.
    """
    _require_intelligence_enabled()
    await _authorize_workspace(principal, "read")
    authorization = http_request.headers.get("authorization", "")
    async with httpx.AsyncClient(timeout=15.0) as http:
        return await agent_runtime.list_tools(app.state.pool, http, KNOWLEDGE_URL, {"Authorization": authorization})


@app.get("/sessions/{session_urn:path}")
async def get_agent_session(session_urn: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "read")
    session = await agent_runtime.get_session(app.state.pool, session_urn)
    return _require_own_session(session, principal)


@app.post("/sessions/{session_urn:path}/turns")
async def run_agent_turn(
    session_urn: str, request: TurnRequest, http_request: Request, principal: Principal = Depends(current_principal)
) -> dict:
    _require_intelligence_enabled()
    await _authorize_workspace(principal, "read")
    await _enforce_spend(principal)
    session = await agent_runtime.get_session(app.state.pool, session_urn)
    _require_own_session(session, principal)
    authorization = http_request.headers.get("authorization", "")
    try:
        body = await agent_runtime.run_turn(
            app.state.pool,
            session_urn=session_urn,
            user_message=request.message,
            knowledge_url=KNOWLEDGE_URL,
            authorization=authorization,
            llm=app.state.llm,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _record_response_tokens(principal, body)
    return body


@app.post("/sessions/{session_urn:path}/replay")
async def replay_agent_session(session_urn: str, principal: Principal = Depends(current_principal)) -> dict:
    _require_intelligence_enabled()
    await _authorize_workspace(principal, "read")
    session = await agent_runtime.get_session(app.state.pool, session_urn)
    _require_own_session(session, principal)
    try:
        return await agent_runtime.replay_session(app.state.pool, session_urn=session_urn, llm=app.state.llm)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/evaluate")
async def evaluate(http_request: Request, principal: Principal = Depends(current_principal)) -> dict:
    """Runs the gold set (whatever is currently in `gold_set_question` —
    none seeded by default) plus the zero-tolerance security suite.
    Auth-only: this triggers real, metered LLM/embedding calls, so a valid
    token is required, same as every other endpoint here.
    """
    _require_intelligence_enabled()
    await _authorize_workspace(principal, "write")
    await _enforce_spend(principal)
    authorization = http_request.headers.get("authorization", "")
    async with httpx.AsyncClient(timeout=15.0) as http:
        response = await http.get(f"{KNOWLEDGE_URL}/glossary", headers={"Authorization": authorization})
        response.raise_for_status()
        glossary_terms = response.json()

    gold_set_result = await evaluation.run_gold_set(
        app.state.pool,
        authorization=authorization,
        knowledge_url=KNOWLEDGE_URL,
        qdrant=app.state.qdrant,
        embedder=app.state.embedder,
        glossary_terms=glossary_terms,
        llm=app.state.llm,
    )
    agent_token, editor_token = _security_probe_tokens()
    security_result = await evaluation.run_security_suite(
        knowledge_url=KNOWLEDGE_URL, agent_token=agent_token, editor_token=editor_token
    )
    return {"goldSet": gold_set_result, "security": security_result}


class RegisterToolPluginRequest(BaseModel):
    entry_point: str


@app.post("/tool-plugins")
async def register_tool_plugin(
    body: RegisterToolPluginRequest, http_request: Request, principal: Principal = Depends(current_principal)
) -> dict:
    """Registers an agent tool plugin. See
    `tool_plugin_registry.py`'s module docstring for details.
    """
    _require_intelligence_enabled()
    await _authorize_workspace(principal, "write")
    authorization = http_request.headers.get("authorization", "")
    async with httpx.AsyncClient(timeout=15.0) as http:
        try:
            return await tool_plugin_registry.register_tool_plugin(
                app.state.pool, http, entry_point=body.entry_point, knowledge_url=KNOWLEDGE_URL,
                headers={"Authorization": authorization},
            )
        except tool_plugin_registry.PluginConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


def _tool_plugin_not_found(name: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"no agent tool plugin registered as {name!r}")


@app.get("/tool-plugins/{name}")
async def get_tool_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "read")
    registration = await tool_plugin_registry.get_tool_plugin_registration(app.state.pool, name)
    if registration is None:
        raise _tool_plugin_not_found(name)
    return registration


@app.post("/tool-plugins/{name}/disable")
async def disable_tool_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    _require_intelligence_enabled()
    await _authorize_workspace(principal, "write")
    registration = await tool_plugin_registry.get_tool_plugin_registration(app.state.pool, name)
    if registration is None:
        raise _tool_plugin_not_found(name)
    return await tool_plugin_registry.set_tool_plugin_status(app.state.pool, name, "disabled")


@app.post("/tool-plugins/{name}/enable")
async def enable_tool_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    _require_intelligence_enabled()
    await _authorize_workspace(principal, "write")
    registration = await tool_plugin_registry.get_tool_plugin_registration(app.state.pool, name)
    if registration is None:
        raise _tool_plugin_not_found(name)
    return await tool_plugin_registry.set_tool_plugin_status(app.state.pool, name, "active")


class RegisterModelRequest(BaseModel):
    version: str
    framework: str = "sklearn"
    artifact_base64: str
    input_schema: dict


@app.post("/models/{name}")
async def register_model(
    name: str, body: RegisterModelRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Register an already-trained model artifact."""
    _require_intelligence_enabled()
    await _authorize_workspace(principal, "write")
    try:
        artifact_bytes = base64.b64decode(body.artifact_base64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"artifact_base64 is not valid base64: {exc}") from exc
    try:
        return await model_registry.register_model(
            app.state.pool,
            app.state.s3,
            MODEL_BUCKET,
            tenant_id=principal.tenant_id,
            name=name,
            version=body.version,
            framework=body.framework,
            artifact_bytes=artifact_bytes,
            input_schema=body.input_schema,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _model_not_found(name: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"no model registered as {name!r}")


@app.get("/models")
async def list_models(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await model_registry.list_models(app.state.pool, principal.tenant_id)


@app.get("/models/{name}")
async def get_model(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "read")
    registration = await model_registry.get_model(app.state.pool, name)
    if registration is None:
        raise _model_not_found(name)
    return registration


@app.post("/models/{name}/disable")
async def disable_model(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    if await model_registry.get_model(app.state.pool, name) is None:
        raise _model_not_found(name)
    return await model_registry.set_model_status(app.state.pool, name, "disabled")


@app.post("/models/{name}/enable")
async def enable_model(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    if await model_registry.get_model(app.state.pool, name) is None:
        raise _model_not_found(name)
    return await model_registry.set_model_status(app.state.pool, name, "active")


class PredictRequest(BaseModel):
    features: dict


@app.post("/models/{name}/predict")
async def predict(name: str, body: PredictRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Real, synchronous inference — not a stub. Authenticated only, no
    workspace-tier gate: same trust boundary Knowledge's own
    `POST /functions/{name}/invoke` already has — a model
    artifact carries no ontology data of its own to protect, whatever
    authorization applies to the *features* passed in is the caller's
    concern, same reasoning stated there.
    """
    await _authorize_workspace(principal, "read")
    if await model_registry.get_model(app.state.pool, name) is None:
        raise _model_not_found(name)
    try:
        prediction = await model_registry.predict(
            app.state.pool, app.state.s3, MODEL_BUCKET, name=name, features=body.features
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"model": name, "prediction": prediction}
