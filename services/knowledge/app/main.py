"""Knowledge Platform — core data access and ontology service."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from holon_common import (
    EventConsumer,
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
    check_iceberg_catalog,
    check_kafka_producer,
    check_opa,
    check_opensearch,
    check_postgres,
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

from . import (
    actions,
    catalog,
    core,
    ontology,
    policy,
    search,
)
from .api import ApiPathRewriteMiddleware, ontologies_router
from .api.public_only import PublicApiOnlyMiddleware
from .routers import actions as actions_router
from .routers import execute as execute_router
from .routers import objects as objects_router
from .routers import ontology_admin as ontology_admin_router
from .routers import plugins as plugins_router
from .core import ICEBERG_CONFIG, TENANT_ID, WORKSPACE_ID

SERVICE_NAME = "knowledge-platform"
configure_json_logging(SERVICE_NAME)
logger = logging.getLogger("knowledge")

DB_URL = os.environ["HOLON_DB_URL"]
IDENTITY_URL = os.environ["HOLON_IDENTITY_URL"]
KAFKA_BOOTSTRAP = os.environ["HOLON_KAFKA_BOOTSTRAP"]
OTLP_ENDPOINT = os.environ.get("HOLON_OTLP_ENDPOINT", "")
OPENSEARCH_URL = os.environ["HOLON_OPENSEARCH_URL"]
OPENSEARCH_PASSWORD = os.environ["HOLON_OPENSEARCH_PASSWORD"]

SPICEDB_URL = os.environ["HOLON_SPICEDB_URL"]
SPICEDB_PRESHARED_KEY = os.environ["HOLON_SPICEDB_PRESHARED_KEY"]
OPA_URL = os.environ["HOLON_OPA_URL"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_production_posture(service_name=SERVICE_NAME)
    # Knowledge-owned tables live in app/migrations (0000_baseline + 0001–).
    # Shared holon_common helpers (audit/outbox) are included in 0000.
    await boot_postgres(
        app, db_url=DB_URL, migrations_dir=Path(__file__).parent / "migrations", deps_module=core
    )
    reset_durable_audit(app.state.pool)
    wire_authz(
        app,
        spicedb_url=SPICEDB_URL,
        spicedb_preshared_key=SPICEDB_PRESHARED_KEY,
        opa_url=OPA_URL,
        deps_module=core,
    )
    policy.bind_authz(app.state.authz)
    await retry_with_backoff(
        lambda: ontology.ensure_authz_seeded_all(
            app.state.authz, app.state.pool, TENANT_ID, WORKSPACE_ID
        ),
        what="knowledge authz seed",
    )

    await retry_with_backoff(
        lambda: search.ensure_index(OPENSEARCH_URL, OPENSEARCH_PASSWORD),
        what="knowledge search index setup",
    )

    outbox_handle = await start_outbox_relay(app.state.pool, kafka_bootstrap=KAFKA_BOOTSTRAP)
    app.state.producer = outbox_handle.producer
    core.producer = outbox_handle.producer

    consumer = EventConsumer(
        KAFKA_BOOTSTRAP,
        topics=["connectivity"],
        group_id="knowledge-platform",
        dlq_producer=app.state.producer,
    )
    ingest_task = asyncio.create_task(
        catalog.consume_events(
            app.state.pool,
            consumer,
            WORKSPACE_ID,
            ICEBERG_CONFIG,
            OPENSEARCH_URL,
            OPENSEARCH_PASSWORD,
        )
    )
    reindex_task = asyncio.create_task(
        catalog.reindex_search_from_serving_store(app.state.pool, OPENSEARCH_URL, OPENSEARCH_PASSWORD)
    )

    status_handle = await start_principal_status(
        kafka_bootstrap=KAFKA_BOOTSTRAP,
        service_name=SERVICE_NAME,
        authz=app.state.authz,
        dlq_producer=app.state.producer,
    )

    expiry_task = asyncio.create_task(actions.sweep_expired_approvals_forever(app.state.pool, WORKSPACE_ID))
    backfill_task = asyncio.create_task(catalog.backfill_join_links(app.state.pool, ICEBERG_CONFIG))

    yield

    await stop_principal_status(status_handle)
    reindex_task.cancel()
    backfill_task.cancel()
    expiry_task.cancel()
    ingest_task.cancel()
    await consumer.stop()
    await stop_outbox(outbox_handle)
    await app.state.authz.aclose()
    await app.state.pool.close()


app = FastAPI(title="Holon — Knowledge Platform", lifespan=lifespan)
app.add_middleware(ApiPathRewriteMiddleware, default_ontology=WORKSPACE_ID)
app.add_middleware(PublicApiOnlyMiddleware)
instrument_cors(app)
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
instrument_correlation(app)
install_error_handlers(app, service_name=SERVICE_NAME)
app.include_router(ontologies_router)
app.include_router(plugins_router.router)
app.include_router(ontology_admin_router.router)
app.include_router(actions_router.router)
app.include_router(objects_router.router)
app.include_router(execute_router.router)


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
            check_opensearch(OPENSEARCH_URL, OPENSEARCH_PASSWORD),
            check_iceberg_catalog(ICEBERG_CONFIG["catalog_uri"], ICEBERG_CONFIG["warehouse"]),
        ],
        extra={
            "join_link_backfill": catalog.join_link_backfill_status,
            "search_reindex": catalog.search_reindex_status,
            "search_skipped_invalid": catalog.search_skipped_invalid,
        },
    )
