"""Intelligence Platform — LLM Gateway, Context Builder, Agent Runtime, Evaluation."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from fastapi import FastAPI
from qdrant_client import AsyncQdrantClient

from holon_common import (
    assert_production_posture,
    configure_json_logging,
    install_error_handlers,
    instrument_cors,
    instrument_metrics,
    instrument_tracing,
    retry_with_backoff,
)
from holon_common.correlation import instrument_correlation
from holon_common.readiness import (
    check_kafka_producer,
    check_opa,
    check_postgres,
    check_qdrant,
    check_spicedb,
    report_ready,
)
from holon_common.service_runtime import (
    boot_postgres,
    reset_durable_audit,
    start_outbox_relay,
    start_principal_status,
    stop_outbox,
    stop_principal_status,
    wire_authz,
)

from . import agent_runtime, deps, vector_store
from .authz_seed import ensure_authz_seeded
from .deps import (
    AWS_ACCESS_KEY_ID,
    AWS_REGION,
    AWS_SECRET_ACCESS_KEY,
    DB_URL,
    KAFKA_BOOTSTRAP,
    KNOWLEDGE_URL,
    OPA_URL,
    OTLP_ENDPOINT,
    QDRANT_URL,
    S3_ENDPOINT,
    SERVICE_NAME,
    SPICEDB_PRESHARED_KEY,
    SPICEDB_URL,
    TENANT_ID,
    WORKSPACE_ID,
    indexer_token,
    intelligence_flag_enabled,
)
from .embeddings import build_embedding_client
from .gold_set import seed_starter_gold_set
from .llm_gateway import build_llm_client
from .routers import router as api_router

configure_json_logging(SERVICE_NAME)
logger = logging.getLogger("intelligence")


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_production_posture(service_name=SERVICE_NAME)
    app.state.intelligence_enabled = intelligence_flag_enabled()
    deps.intelligence_enabled = app.state.intelligence_enabled

    # Intelligence-owned tables live in app/migrations (0000_baseline).
    # Shared holon_common helpers (audit/outbox/plugin) are included in 0000.
    await boot_postgres(
        app, db_url=DB_URL, migrations_dir=Path(__file__).parent / "migrations", deps_module=deps
    )
    async with app.state.pool.acquire() as conn:
        await seed_starter_gold_set(conn)

    reset_durable_audit(app.state.pool)
    wire_authz(
        app,
        spicedb_url=SPICEDB_URL,
        spicedb_preshared_key=SPICEDB_PRESHARED_KEY,
        opa_url=OPA_URL,
        deps_module=deps,
    )
    await retry_with_backoff(
        lambda: ensure_authz_seeded(app.state.authz, app.state.pool),
        what="intelligence authz seed",
    )

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
    deps.s3 = app.state.s3

    outbox_handle = await start_outbox_relay(app.state.pool, kafka_bootstrap=KAFKA_BOOTSTRAP)
    app.state.producer = outbox_handle.producer

    app.state.embedder = build_embedding_client()
    deps.embedder = app.state.embedder
    if not app.state.intelligence_enabled:
        os.environ.setdefault("HOLON_LLM_PROVIDER", "fake")
    app.state.llm = build_llm_client()
    deps.llm = app.state.llm

    app.state.qdrant = AsyncQdrantClient(url=QDRANT_URL, check_compatibility=False, timeout=10)
    deps.qdrant = app.state.qdrant
    await retry_with_backoff(
        lambda: vector_store.ensure_collection(app.state.qdrant, app.state.embedder.dimension),
        what="qdrant collection setup",
    )
    await retry_with_backoff(
        lambda: vector_store.maybe_rebuild_collection(app.state.qdrant, app.state.embedder.dimension),
        what="qdrant optional rebuild",
    )
    await retry_with_backoff(
        lambda: vector_store.purge_untagged_points(app.state.qdrant),
        what="qdrant purge untagged points",
    )
    indexed = await retry_with_backoff(
        lambda: vector_store.index_metadata(
            app.state.qdrant,
            app.state.embedder,
            knowledge_url=KNOWLEDGE_URL,
            token=indexer_token(),
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
        ),
        what="semantic index build",
    )
    logger.info(
        "indexed %d metadata documents into Qdrant (intelligence_enabled=%s)",
        indexed,
        app.state.intelligence_enabled,
    )

    sweep_task = asyncio.create_task(agent_runtime.sweep_expired_sessions_forever(app.state.pool))

    status_handle = await start_principal_status(
        kafka_bootstrap=KAFKA_BOOTSTRAP,
        service_name=SERVICE_NAME,
        authz=app.state.authz,
        dlq_producer=app.state.producer,
    )

    yield

    await stop_principal_status(status_handle)
    sweep_task.cancel()
    await stop_outbox(outbox_handle)
    await app.state.authz.aclose()
    await app.state.qdrant.close()
    await app.state.pool.close()


app = FastAPI(title="Holon — Ontology-grounded agent runtime (beta)", lifespan=lifespan)
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
            check_kafka_producer(app.state.producer),
            check_qdrant(QDRANT_URL),
        ],
        extra={"intelligence_enabled": bool(getattr(app.state, "intelligence_enabled", True))},
    )
