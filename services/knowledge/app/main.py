"""Knowledge Platform — core data access and ontology service.

Owns the catalog, the conceptual ontology and the lineage graph. Reads
never touch a table directly: every consumer — dashboard today,
agents later — goes through the ontology API exposed here, and every read
of a governed resource goes through the PDP policy engine instead of a flat
tenant check.

Object reads go through `serving_store` via `_resolve_one`/`_resolve_many`,
not a live Iceberg/DuckDB scan per request — materialization happens once
per sync, in `catalog.py`. A serving-store miss (nothing materialized yet
for that key) degrades to a live scan through `resolver.py` instead of a
false 404 or a 500; that fallback call uses `asyncio.to_thread` because
pyiceberg/DuckDB are synchronous, ensuring the event loop is not blocked
during live scans.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from holon_common import (
    EventConsumer,
    EventProducer,
    configure_json_logging,
    create_pool,
    install_error_handlers,
    instrument_cors,
    instrument_metrics,
    instrument_tracing,
    outbox,
    retry_with_backoff,
)
from holon_common.authz import PermissionClient

from . import (
    actions,
    catalog,
    core,
    execution,
    execution_adapter_registry,
    export_format_registry,
    function_registry,
    glossary,
    lineage,
    ontology,
    query_log,
    search,
    serving_store,
)
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
OTLP_ENDPOINT = os.environ["HOLON_OTLP_ENDPOINT"]
OPENSEARCH_URL = os.environ["HOLON_OPENSEARCH_URL"]
OPENSEARCH_PASSWORD = os.environ["HOLON_OPENSEARCH_PASSWORD"]

SPICEDB_URL = os.environ["HOLON_SPICEDB_URL"]
SPICEDB_PRESHARED_KEY = os.environ["HOLON_SPICEDB_PRESHARED_KEY"]
OPA_URL = os.environ["HOLON_OPA_URL"]
SPICEDB_SCHEMA_PATH = os.environ["HOLON_SPICEDB_SCHEMA_PATH"]


async def _consume_identity_events(consumer: EventConsumer) -> None:
    """Decision cache event-driven invalidation.
    `identity.permission.revoked` fires from Identity's `/access/revoke`;
    every cached decision naming that principal — as either the acting
    principal or a mandant — is purged the instant it arrives, not left to
    expire on the TTL alone.
    """
    await consumer.start()
    async for event in consumer:
        try:
            if event.event_type == "identity.permission.revoked":
                purged = app.state.authz.invalidate_principal(event.payload["principal_urn"])
                logger.info(
                    "authz cache: purged %d entr%s for revoked principal %s",
                    purged, "y" if purged == 1 else "ies", event.payload["principal_urn"],
                )
        except Exception:
            logger.exception("failed to process identity event %s for authz cache invalidation", event.event_id)
        await consumer.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool(DB_URL)
    core.pool = app.state.pool
    async with app.state.pool.acquire() as conn:
        await catalog.ensure_schema(conn)
        await lineage.ensure_schema(conn)
        await ontology.ensure_schema(conn)
        await actions.ensure_schema(conn)
        await serving_store.ensure_schema(conn)
        await execution.ensure_schema(conn)
        await execution_adapter_registry.ensure_schema(conn)
        await export_format_registry.ensure_schema(conn)
        await function_registry.ensure_schema(conn)
        await glossary.ensure_schema(conn)
        await query_log.ensure_schema(conn)
        await outbox.ensure_schema(conn)
    await ontology.ensure_seeded(app.state.pool, TENANT_ID, WORKSPACE_ID)
    await glossary.ensure_seeded(app.state.pool, TENANT_ID, WORKSPACE_ID, ontology.object_type_urn)

    app.state.authz = PermissionClient(SPICEDB_URL, SPICEDB_PRESHARED_KEY, OPA_URL)
    core.authz = app.state.authz
    await retry_with_backoff(
        lambda: ontology.ensure_authz_seeded(app.state.authz, SPICEDB_SCHEMA_PATH, TENANT_ID, WORKSPACE_ID),
        what="knowledge authz seed",
    )

    # `depends_on: opensearch: condition: service_healthy` should mean
    # OpenSearch is ready by the time this runs, but retry logic ensures
    # robust startup readiness.
    await retry_with_backoff(
        lambda: search.ensure_index(OPENSEARCH_URL, OPENSEARCH_PASSWORD), what="knowledge search index setup"
    )

    # Read straight from OPA's data API instead of duplicating
    # `holon.rego`'s `allowed_countries` in Python, preventing data drift.
    # Policy data is fetched at startup.
    app.state.allowed_countries = set(
        await retry_with_backoff(
            lambda: app.state.authz.get_policy_data("holon/authz/allowed_countries"),
            what="OPA allowed_countries fetch",
        )
    )
    core.allowed_countries = app.state.allowed_countries

    app.state.producer = EventProducer(KAFKA_BOOTSTRAP)
    await app.state.producer.start()
    core.producer = app.state.producer
    relay_task = asyncio.create_task(outbox.relay_forever(app.state.pool, app.state.producer, dlq_producer=app.state.producer))

    consumer = EventConsumer(
        KAFKA_BOOTSTRAP, topics=["connectivity"], group_id="knowledge-platform", dlq_producer=app.state.producer
    )
    ingest_task = asyncio.create_task(
        catalog.consume_events(
            app.state.pool,
            consumer,
            WORKSPACE_ID,
            ICEBERG_CONFIG,
            OPENSEARCH_URL,
            OPENSEARCH_PASSWORD,
            app.state.allowed_countries,
        )
    )

    # Decision cache event-driven invalidation.
    # Own consumer group: a distinct concern from cataloguing, on a
    # different topic entirely.
    authz_cache_consumer = EventConsumer(
        KAFKA_BOOTSTRAP,
        topics=["identity"],
        group_id="knowledge-platform-authz-cache-invalidation",
        dlq_producer=app.state.producer,
    )
    authz_cache_invalidation_task = asyncio.create_task(_consume_identity_events(authz_cache_consumer))

    expiry_task = asyncio.create_task(actions.sweep_expired_approvals_forever(app.state.pool, WORKSPACE_ID))

    yield

    expiry_task.cancel()
    authz_cache_invalidation_task.cancel()
    ingest_task.cancel()
    relay_task.cancel()
    await authz_cache_consumer.stop()
    await consumer.stop()
    await app.state.producer.stop()
    await app.state.authz.aclose()
    await app.state.pool.close()


app = FastAPI(title="Holon — Knowledge Platform", lifespan=lifespan)
instrument_cors(app)
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
install_error_handlers(app, service_name=SERVICE_NAME)
app.include_router(plugins_router.router)
app.include_router(ontology_admin_router.router)
# `actions_router` before `objects_router`: the specific
# `/objects/Customer/{customer_id}/actions/{putOnCreditHold|closeAccount}`
# routes (actions_router) must win route-matching over objects_router's
# generic `POST /objects/{object_type}/{instance_id}/actions/{action_name}`
# (the declarative Action Type invocation endpoint) — Starlette matches
# by registration order, same "specific before generic" requirement
# objects.py's own module docstring already documents for its
# `/export` vs `/{instance_id}` routes, one router up.
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
    await app.state.pool.fetchval("SELECT 1")
    return {"status": "ok"}
