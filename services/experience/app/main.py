"""Experience Platform — Serves the React SPA and Application Builder API."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Request

from holon_common import (
    CircuitBreaker,
    PermissionClient,
    Principal,
    assert_production_posture,
    configure_json_logging,
    create_pool,
    install_error_handlers,
    instrument_cors,
    instrument_metrics,
    instrument_tracing,
    retry_with_backoff,
    run_migrations,
    is_production,
)
from holon_common.correlation import instrument_correlation
from holon_common.principal_status import (
    consume_identity_auth_events,
    hydrate_revocation_snapshot,
    make_principal_status_consumer,
    refresh_revocation_snapshot_forever,
)
from holon_common.audit import clear_durable_audit_hooks
from holon_common.audit_store import install_durable_audit, list_events, list_events_page
from holon_common.readiness import check_kafka_bootstrap, check_opa, check_postgres, check_spicedb, report_ready

from . import application_builder, deps
from .deps import (
    AUTOMATION_URL,
    CONNECTIVITY_URL,
    DB_URL,
    IDENTITY_URL,
    INTELLIGENCE_URL,
    KAFKA_BOOTSTRAP,
    KNOWLEDGE_URL,
    OPA_URL,
    OTLP_ENDPOINT,
    SERVICE_NAME,
    SPICEDB_PRESHARED_KEY,
    SPICEDB_URL,
    TENANT_ID,
    WORKSPACE_ID,
    WORKSPACE_URN,
    _authorize_workspace,
    _get_json,
    _intelligence_enabled,
    _upstream_authorization,
    current_principal,
)
from .routers import router as api_router
from .routers.spa import router as spa_router

configure_json_logging(SERVICE_NAME)

_TIMEOUT_SECONDS = 5.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_production_posture(service_name=SERVICE_NAME)
    app.state.client = httpx.AsyncClient(
        timeout=_TIMEOUT_SECONDS, limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
    )
    deps.client = app.state.client
    app.state.breaker = CircuitBreaker(name="experience-proxy", failure_threshold=5, cooldown_seconds=30.0)
    deps.breaker = app.state.breaker

    app.state.pool = await create_pool(DB_URL)
    deps.pool = app.state.pool
    # Experience-owned tables live in app/migrations/0000_baseline.sql.
    await run_migrations(app.state.pool, Path(__file__).parent / "migrations")

    clear_durable_audit_hooks()
    install_durable_audit(app.state.pool)

    app.state.authz = PermissionClient(SPICEDB_URL, SPICEDB_PRESHARED_KEY, OPA_URL)
    deps.authz = app.state.authz

    async def _seed_application_authz() -> None:
        backfilled = await application_builder.backfill_urns(
            app.state.pool, tenant_id=TENANT_ID, workspace_id=WORKSPACE_ID,
        )
        for name in backfilled:
            await app.state.authz.write_relationship(
                resource_type="application",
                resource_urn=application_builder.application_urn(TENANT_ID, WORKSPACE_ID, name),
                relation="parent_workspace",
                subject_type="workspace",
                subject_urn=WORKSPACE_URN,
            )

    await retry_with_backoff(_seed_application_authz, what="experience authz seed")

    status_consumer = make_principal_status_consumer(KAFKA_BOOTSTRAP, service_name=SERVICE_NAME)
    status_task = asyncio.create_task(consume_identity_auth_events(status_consumer, authz=app.state.authz))
    await retry_with_backoff(hydrate_revocation_snapshot, what="identity revocation snapshot")
    revocation_refresh_task = asyncio.create_task(refresh_revocation_snapshot_forever())

    yield
    revocation_refresh_task.cancel()
    status_task.cancel()
    await status_consumer.stop()
    await app.state.pool.close()
    await app.state.client.aclose()


app = FastAPI(title="Holon — Experience Platform", lifespan=lifespan)
instrument_cors(app)
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
instrument_correlation(app)
install_error_handlers(app, service_name=SERVICE_NAME)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/live")
async def live() -> dict:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict:
    return await report_ready(
        [
            check_postgres(app.state.pool),
            check_spicedb(SPICEDB_URL, SPICEDB_PRESHARED_KEY),
            check_opa(OPA_URL),
            check_kafka_bootstrap(KAFKA_BOOTSTRAP),
        ]
    )


@app.get("/api/config")
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


@app.get("/api/audit-events")
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
    return await list_events_page(
        app.state.pool,
        principal.tenant_id,
        category=category,
        action=action,
        actor_urn=actor,
        outcome=outcome,
        trace_id=traceId,
        page_size=50 if pageSize is None else pageSize,
        page_token=pageToken,
    )


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


@app.get("/api/audit-events/trace/{trace_id}")
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

    async def fetch(service: str, url: str) -> tuple[str, int, object]:
        try:
            status, body = await _get_json(f"{url}?{query}", authorization=authorization)
        except httpx.HTTPError as exc:
            return service, 503, {"detail": str(exc)}
        return service, status, body

    local = await list_events(
        app.state.pool, principal.tenant_id, trace_id=trace_id, page_size=_AUDIT_TRACE_PAGE_SIZE + 1
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


app.include_router(api_router)
# After every API route. A missing hashed asset stays 404, not index.html.
app.include_router(spa_router)
