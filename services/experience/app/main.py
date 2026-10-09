"""Experience Platform — Serves the React SPA and Application Builder API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from holon_common import (
    CircuitBreaker,
    assert_production_posture,
    configure_json_logging,
    install_error_handlers,
    instrument_cors,
    instrument_metrics,
    instrument_tracing,
    retry_with_backoff,
)
from holon_common.correlation import instrument_correlation
from holon_common.readiness import check_kafka_bootstrap, check_opa, check_postgres, check_spicedb, report_ready
from holon_common.service_runtime import (
    boot_postgres,
    reset_durable_audit,
    start_principal_status,
    stop_principal_status,
    wire_authz,
)

from . import application_builder, deps
from .deps import (
    DB_URL,
    KAFKA_BOOTSTRAP,
    OPA_URL,
    OTLP_ENDPOINT,
    SERVICE_NAME,
    SPICEDB_PRESHARED_KEY,
    SPICEDB_URL,
    TENANT_ID,
    WORKSPACE_ID,
    WORKSPACE_URN,
    _TIMEOUT_SECONDS,
)
from .routers import router as api_router

configure_json_logging(SERVICE_NAME)

STATIC_DIR = Path(__file__).parent / "static"

# Hashed Vite chunks under /assets/ can be cached forever. index.html and
# unhashed files must revalidate — a cached shell after `npm run build`
# points at deleted filenames.
_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
_HTML_CACHE_CONTROL = "no-cache, must-revalidate"
_STATIC_ASSET_SUFFIXES = {
    ".css",
    ".eot",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".map",
    ".png",
    ".svg",
    ".ttf",
    ".webp",
    ".woff",
    ".woff2",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_production_posture(service_name=SERVICE_NAME)
    app.state.client = httpx.AsyncClient(
        timeout=_TIMEOUT_SECONDS, limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
    )
    app.state.breaker = CircuitBreaker(name="experience-proxy", failure_threshold=5, cooldown_seconds=30.0)
    deps.client = app.state.client
    deps.breaker = app.state.breaker

    # Experience-owned tables live in app/migrations/0000_baseline.sql.
    await boot_postgres(
        app, db_url=DB_URL, migrations_dir=Path(__file__).parent / "migrations", deps_module=deps
    )
    reset_durable_audit(app.state.pool)
    wire_authz(
        app,
        spicedb_url=SPICEDB_URL,
        spicedb_preshared_key=SPICEDB_PRESHARED_KEY,
        opa_url=OPA_URL,
        deps_module=deps,
    )

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

    status_handle = await start_principal_status(
        kafka_bootstrap=KAFKA_BOOTSTRAP,
        service_name=SERVICE_NAME,
        authz=app.state.authz,
    )

    yield
    await stop_principal_status(status_handle)
    await app.state.pool.close()
    await app.state.client.aclose()


app = FastAPI(title="Holon — Experience Platform", lifespan=lifespan)
instrument_cors(app)
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
instrument_correlation(app)
install_error_handlers(app, service_name=SERVICE_NAME)
app.include_router(api_router)


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


def _looks_like_static_asset(full_path: str) -> bool:
    if full_path.startswith("assets/"):
        return True
    return Path(full_path).suffix.lower() in _STATIC_ASSET_SUFFIXES


@app.get("/{full_path:path}")
async def spa(full_path: str) -> FileResponse:
    """SPA shell + static asset server.

    Registered last so every real API route above wins the match first.
    Serves the requested file if it exists under STATIC_DIR (built JS/CSS
    chunks, favicon, ...); otherwise falls back to index.html so the
    client-side router can render deep links (e.g. a hard refresh on
    `/applications/foo`). Missing hashed assets return 404 — never HTML —
    so a stale import does not execute the shell as a module.
    """
    index = STATIC_DIR / "index.html"
    candidate = (STATIC_DIR / full_path).resolve()
    if full_path and candidate.is_file() and STATIC_DIR.resolve() in candidate.parents:
        cache = _ASSET_CACHE_CONTROL if full_path.startswith("assets/") else _HTML_CACHE_CONTROL
        return FileResponse(candidate, headers={"Cache-Control": cache})
    if _looks_like_static_asset(full_path):
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(index, headers={"Cache-Control": _HTML_CACHE_CONTROL})
