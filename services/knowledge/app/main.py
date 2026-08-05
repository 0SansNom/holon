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
false 404 or a 500; that fallback call is the one place `asyncio.to_thread`
still matters here — pyiceberg/DuckDB are synchronous, and calling them
directly from an `async def` handler blocks this process's single event
loop for the scan's duration, starving the Kafka consumer's heartbeat, the
outbox relay, and every concurrent request at once. Not a theoretical concern:
this is exactly what produced the Kafka "coordinator dead" errors and
httpx timeouts to SpiceDB/OPA seen under load before this was first fixed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from holon_common import (
    EventConsumer,
    EventProducer,
    Principal,
    build_urn,
    configure_json_logging,
    create_pool,
    instrument_cors,
    instrument_metrics,
    instrument_tracing,
    make_principal_dependency,
    outbox,
    retry_with_backoff,
)
from holon_common.authz import PermissionClient

from . import (
    actions,
    catalog,
    execution,
    execution_adapter_registry,
    export_format_registry,
    glossary,
    lineage,
    ontology,
    query_log,
    resolver,
    search,
    serving_store,
)

SERVICE_NAME = "knowledge-platform"
configure_json_logging(SERVICE_NAME)
logger = logging.getLogger("knowledge")

TENANT_ID = os.environ["HOLON_TENANT_ID"]
WORKSPACE_ID = os.environ["HOLON_WORKSPACE_ID"]
JWT_SECRET = os.environ["HOLON_JWT_SECRET"]
DB_URL = os.environ["HOLON_DB_URL"]
KAFKA_BOOTSTRAP = os.environ["HOLON_KAFKA_BOOTSTRAP"]
OTLP_ENDPOINT = os.environ["HOLON_OTLP_ENDPOINT"]
OPENSEARCH_URL = os.environ["HOLON_OPENSEARCH_URL"]
OPENSEARCH_PASSWORD = os.environ["HOLON_OPENSEARCH_PASSWORD"]
SPICEDB_URL = os.environ["HOLON_SPICEDB_URL"]
SPICEDB_PRESHARED_KEY = os.environ["HOLON_SPICEDB_PRESHARED_KEY"]
OPA_URL = os.environ["HOLON_OPA_URL"]
SPICEDB_SCHEMA_PATH = os.environ["HOLON_SPICEDB_SCHEMA_PATH"]

ICEBERG_CONFIG = dict(
    catalog_uri=os.environ["HOLON_ICEBERG_CATALOG_URI"],
    warehouse=os.environ["HOLON_ICEBERG_WAREHOUSE"],
    s3_endpoint=os.environ["HOLON_S3_ENDPOINT"],
    access_key=os.environ["AWS_ACCESS_KEY_ID"],
    secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    region=os.environ["AWS_REGION"],
)

CUSTOMER_OBJECT_TYPE_URN = ontology.customer_object_type_urn(TENANT_ID, WORKSPACE_ID)
ORDER_OBJECT_TYPE_URN = ontology.order_object_type_urn(TENANT_ID, WORKSPACE_ID)
SUPPORT_TICKET_OBJECT_TYPE_URN = ontology.support_ticket_object_type_urn(TENANT_ID, WORKSPACE_ID)
PRODUCT_REVIEW_OBJECT_TYPE_URN = ontology.product_review_object_type_urn(TENANT_ID, WORKSPACE_ID)
SUPPLIER_OBJECT_TYPE_URN = ontology.supplier_object_type_urn(TENANT_ID, WORKSPACE_ID)
INVENTORY_LEVEL_OBJECT_TYPE_URN = ontology.inventory_level_object_type_urn(TENANT_ID, WORKSPACE_ID)
OBJECT_TYPE_URNS = {
    "Customer": CUSTOMER_OBJECT_TYPE_URN,
    "Order": ORDER_OBJECT_TYPE_URN,
    "SupportTicket": SUPPORT_TICKET_OBJECT_TYPE_URN,
    "ProductReview": PRODUCT_REVIEW_OBJECT_TYPE_URN,
    "Supplier": SUPPLIER_OBJECT_TYPE_URN,
    "InventoryLevel": INVENTORY_LEVEL_OBJECT_TYPE_URN,
}

# The export endpoint needs the same fetch_fn every existing
# list endpoint already uses — one small mapping, not a new read path.
FETCH_FNS = {
    "Customer": resolver.fetch_customers,
    "Order": resolver.fetch_orders,
    "SupportTicket": resolver.fetch_support_tickets,
    "ProductReview": resolver.fetch_reviews,
    "Supplier": resolver.fetch_suppliers,
    "InventoryLevel": resolver.fetch_inventory_levels,
}


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
    async with app.state.pool.acquire() as conn:
        await catalog.ensure_schema(conn)
        await lineage.ensure_schema(conn)
        await ontology.ensure_schema(conn)
        await actions.ensure_schema(conn)
        await serving_store.ensure_schema(conn)
        await execution.ensure_schema(conn)
        await execution_adapter_registry.ensure_schema(conn)
        await export_format_registry.ensure_schema(conn)
        await glossary.ensure_schema(conn)
        await query_log.ensure_schema(conn)
        await outbox.ensure_schema(conn)
    await ontology.ensure_seeded(app.state.pool, TENANT_ID, WORKSPACE_ID)
    await glossary.ensure_seeded(app.state.pool, TENANT_ID, WORKSPACE_ID, ontology.object_type_urn)

    app.state.authz = PermissionClient(SPICEDB_URL, SPICEDB_PRESHARED_KEY, OPA_URL)
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

    # Read straight from OPA's own data API instead of hand-copying
    # `holon.rego`'s `allowed_countries` into a Python literal — avoids a
    # two-sources-of-truth drift. OPA's policy is static in this build
    # (baked into its container image, no reload mechanism), so a single
    # fetch at startup is sufficient today; a dynamically-reloadable
    # policy would need this refreshed, not just fetched once.
    app.state.allowed_countries = set(
        await retry_with_backoff(
            lambda: app.state.authz.get_policy_data("holon/authz/allowed_countries"),
            what="OPA allowed_countries fetch",
        )
    )

    app.state.producer = EventProducer(KAFKA_BOOTSTRAP)
    await app.state.producer.start()
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
current_principal = make_principal_dependency(JWT_SECRET)


async def _authorize_object_type(principal: Principal, object_type_urn: str, permission: str) -> None:
    """Shared by every object-type endpoint, read or write, Customer or
    Order (the PDP doesn't care which resource or verb it's checking —
    only `object_type_urn` and `permission` change).

    Behavior for `permission == "read"`: classification used to be passed
    straight through as the resource attribute, so OPA's
    `allow := false if classification == confidential and country not
    allowed` denied the *entire* object type wholesale for a
    disallowed-country principal — even its non-confidential properties.
    That's now handled at the correct granularity: `_mask_confidential_properties`
    masks only the actually-confidential fields, applied uniformly at the
    read choke point (`_resolve_one`/`_resolve_many`).
    ReBAC (can this principal read objects of this type at all) is unaffected —
    only the object-level ABAC classification gate is skipped for reads,
    since classification enforcement for reads now lives at the property
    level. Writes/approvals (`putOnCreditHold`, `closeAccount`, approving
    them) keep the original all-or-nothing check: masking has no meaning
    for a mutation — you can't partially deny writing to a field the
    request never even names them individually.
    """
    object_type = await ontology.get_object_type(app.state.pool, object_type_urn)
    if object_type is None:
        # Seeded at startup (ensure_seeded) — reaching here means that failed, not that
        # the resource is merely undefined. Fail loudly rather than guess a classification.
        raise HTTPException(status_code=500, detail=f"ObjectType {object_type_urn} is not catalogued")

    resource_attributes = {} if permission == "read" else {"classification": object_type["classification"]}
    decision = await app.state.authz.authorize(
        principal,
        resource_type="object_type",
        resource_urn=object_type_urn,
        permission=permission,
        resource_attributes=resource_attributes,
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)


async def _mask_confidential_properties(object_type_urn: str, principal: Principal, rows: list[dict]) -> list[dict]:
    """Row/column security enforcement point. A confidential
    property is replaced with `None` (and named in `_maskedFields`) rather
    than the whole object being withheld; a principal whose country
    passes ABAC gets every field, unmasked. Uses the same OPA-sourced
    `allowed_countries` set `search.py` mirrors (`app.state.allowed_countries`,
    fetched once at startup via `PermissionClient.get_policy_data`) —
    consistent single source of truth, not a second hand-copied policy.
    """
    if principal.country in app.state.allowed_countries:
        return rows
    property_classifications = await ontology.get_property_classifications(app.state.pool, object_type_urn)
    confidential_properties = {name for name, classification in property_classifications.items() if classification == "confidential"}
    if not confidential_properties:
        return rows

    masked_rows = []
    for row in rows:
        row = dict(row)
        masked_fields = [name for name in confidential_properties if row.get(name) is not None]
        for name in masked_fields:
            row[name] = None
        if masked_fields:
            row["_maskedFields"] = masked_fields
        masked_rows.append(row)
    return masked_rows


async def _resolve_one(
    object_type: str,
    tenant_id: str,
    instance_id,
    fetch_fn,
    id_kwarg: str,
    *,
    principal: Principal,
    as_of: Optional[datetime] = None,
) -> Optional[dict]:
    """Serving-store read with a federated fallback: a miss
    here means nothing has been materialized for this key yet, not that
    the object doesn't exist — so degrade to a live scan via `resolver.py`
    rather than a false 404 or a 500.

    `as_of` historical read takes a different path entirely:
    a historical read either has recorded history to answer from or it
    doesn't, so it bypasses *both* the live serving-store read and the
    federated fallback above — degrading to "current" would silently answer
    a different question than the one asked.

    Property masking is applied here unconditionally — this function (and
    `_resolve_many` below) is the single read choke point,
    so every one of the dozen-plus object-read endpoints gets it without a per-endpoint call.
    """
    object_type_urn = OBJECT_TYPE_URNS[object_type]

    if as_of is not None:
        row = await serving_store.get_instance_as_of(app.state.pool, object_type, tenant_id, instance_id, as_of)
        if row is None:
            return None
        return (await _mask_confidential_properties(object_type_urn, principal, [row]))[0]

    data = await serving_store.get_instance(app.state.pool, object_type, tenant_id, instance_id)
    if data is not None:
        return (await _mask_confidential_properties(object_type_urn, principal, [data]))[0]
    rows = await asyncio.to_thread(fetch_fn, **{id_kwarg: instance_id}, **ICEBERG_CONFIG)
    if not rows:
        return None
    row = dict(rows[0])
    row["materializedAt"] = None
    row["sourceLagSeconds"] = 0
    row["degraded"] = True
    return (await _mask_confidential_properties(object_type_urn, principal, [row]))[0]


async def _resolve_many(
    object_type: str,
    tenant_id: str,
    fetch_fn,
    *,
    principal: Principal,
    filter_column: Optional[str] = None,
    filter_kwarg: Optional[str] = None,
    filter_value=None,
) -> list[dict]:
    object_type_urn = OBJECT_TYPE_URNS[object_type]
    rows = await serving_store.list_instances(
        app.state.pool, object_type, tenant_id, filter_column=filter_column, filter_value=filter_value
    )
    if rows:
        return await _mask_confidential_properties(object_type_urn, principal, rows)
    kwargs = {filter_kwarg: filter_value} if filter_kwarg else {}
    live_rows = await asyncio.to_thread(fetch_fn, **kwargs, **ICEBERG_CONFIG)
    result = []
    for row in live_rows:
        row = dict(row)
        row["materializedAt"] = None
        row["sourceLagSeconds"] = 0
        row["degraded"] = True
        result.append(row)
    return await _mask_confidential_properties(object_type_urn, principal, result)


async def _merge_action_overlays(rows: list[dict]) -> list[dict]:
    customer_ids = [row["id"] for row in rows]
    holds = await actions.get_credit_holds(app.state.pool, customer_ids)
    statuses = await actions.get_account_status(app.state.pool, customer_ids)
    for row in rows:
        hold = holds.get(row["id"])
        row["credit_hold"] = bool(hold["on_hold"]) if hold else False
        row["credit_hold_reason"] = hold["reason"] if hold else None
        status = statuses.get(row["id"])
        row["account_closed"] = bool(status["closed"]) if status else False
        row["account_closed_reason"] = status["reason"] if status else None
    return rows


def _approval_object_type_urn(approval: dict) -> str:
    definition = actions.ACTION_DEFINITIONS[approval["action_name"]]
    return OBJECT_TYPE_URNS[definition["target_object_type"]]


class ActionRequest(BaseModel):
    reason: str
    ttl_seconds: Optional[int] = None
    """Test/demo-only override of the default 24h approval review window
    — same treatment as Connectivity's
    `CLOSE_ACCOUNT_FAILURE_SENTINEL`. Ignored entirely for low-risk Actions,
    which never create an `action_approval` row in the first place.
    """


class ApprovalDecisionRequest(BaseModel):
    note: Optional[str] = None


class CompensationRequest(BaseModel):
    error: str


class RelationTypeRequest(BaseModel):
    name: str
    source_object_type: str
    target_object_type: str
    source_property: str
    cardinality: str


class ExecutionRequest(BaseModel):
    object_type: str
    filter_property: str
    filter_value: str
    operation: str = "filter"  # "filter" | "count" — see execution.py's module docstring


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


@app.get("/catalog/datasets")
async def get_datasets(principal: Principal = Depends(current_principal)) -> list[dict]:
    return await catalog.list_datasets(app.state.pool, principal.tenant_id)


@app.get("/ontology")
async def list_ontology_definitions(principal: Principal = Depends(current_principal)) -> list[dict]:
    """A real, previously-missing gap: every other governed resource type
    (`RelationType`, `Action`) already had a list endpoint; `ObjectType`
    never did — every existing caller already knew the six hardcoded
    names. Same auth-only convention as `/relation-types`/`/actions`.
    """
    return await ontology.list_object_types(app.state.pool, principal.tenant_id)


@app.get("/ontology/{name}")
async def get_ontology_definition(name: str, principal: Principal = Depends(current_principal)) -> dict:
    """Inspects an ObjectType *definition* — property mapping, computed
    classification — as opposed to `/objects/{name}` which resolves its
    *instances*. Metadata, not data: gated by authentication only, like
    `/catalog/datasets`, not by the PDP (row/column security has
    nothing to enforce on a definition with no rows).
    """
    object_type_urn = OBJECT_TYPE_URNS.get(name)
    if object_type_urn is None:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {name}")
    object_type = await ontology.get_object_type(app.state.pool, object_type_urn)
    if object_type is None:
        raise HTTPException(status_code=500, detail=f"ObjectType {object_type_urn} is not catalogued")
    return object_type


async def _authorize_ontology_governance(principal: Principal) -> None:
    """Ontology lifecycle changes (versioning/publication) are a
    governance action, same tier as `create_relation_type` — the
    workspace's own `approve` permission (admin-only), not
    `_authorize_object_type` (there's no read/write of instance data
    happening here).
    """
    decision = await app.state.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=ontology.workspace_urn(principal.tenant_id, WORKSPACE_ID),
        permission="approve",
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)


class ProposeObjectTypeVersionRequest(BaseModel):
    property_mapping: Optional[dict] = None
    description: Optional[str] = None


@app.post("/ontology/{name}/versions", status_code=201)
async def propose_object_type_version(
    name: str, request: ProposeObjectTypeVersionRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Ontology lifecycle (a previously-flagged real gap: no ObjectType
    versioning/publication existed at all). Creates a `draft` — never
    touches the live definition every other read path uses until
    `POST /ontology/{name}/versions/{version}/publish` says otherwise.
    """
    await _authorize_ontology_governance(principal)
    object_type_urn = OBJECT_TYPE_URNS.get(name)
    if object_type_urn is None:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {name}")
    try:
        return await ontology.propose_object_type_version(
            app.state.pool,
            object_type_urn=object_type_urn,
            property_mapping=request.property_mapping,
            description=request.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/ontology/{name}/versions")
async def list_object_type_versions(name: str, principal: Principal = Depends(current_principal)) -> list[dict]:
    object_type_urn = OBJECT_TYPE_URNS.get(name)
    if object_type_urn is None:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {name}")
    return await ontology.list_object_type_versions(app.state.pool, object_type_urn)


@app.post("/ontology/{name}/versions/{version}/publish")
async def publish_object_type_version(
    name: str, version: int, principal: Principal = Depends(current_principal)
) -> dict:
    """Publishes transactional outbox event `knowledge.objecttype.published`
    and updates the live `object_type` row — the only thing that
    ever does, past its bootstrap-seeded state.
    """
    await _authorize_ontology_governance(principal)
    object_type_urn = OBJECT_TYPE_URNS.get(name)
    if object_type_urn is None:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {name}")
    try:
        return await ontology.publish_object_type_version(app.state.pool, object_type_urn=object_type_urn, version=version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/query-log")
async def get_query_log(principal: Principal = Depends(current_principal)) -> list[dict]:
    """Read surface for the anonymized query log, so it's genuinely
    inspectable rather than write-only. Auth-only, tenant-scoped.
    """
    return await query_log.list_recent(app.state.pool, principal.tenant_id)


@app.get("/glossary")
async def list_glossary(principal: Principal = Depends(current_principal)) -> list[dict]:
    """Populated business glossary endpoint. Auth-only,
    same convention as `/ontology/{name}` — metadata, not instance data.
    """
    return await glossary.list_terms(app.state.pool, principal.tenant_id)


@app.get("/glossary/{term}")
async def get_glossary_term(term: str, principal: Principal = Depends(current_principal)) -> dict:
    result = await glossary.get_term(app.state.pool, principal.tenant_id, term)
    if result is None:
        raise HTTPException(status_code=404, detail=f"unknown glossary term: {term!r}")
    return result


@app.get("/actions")
async def list_actions(principal: Principal = Depends(current_principal)) -> list[dict]:
    """Read surface for `actions.ACTION_DEFINITIONS` — same auth-only
    convention as `/ontology/{name}`/`/relation-types` (metadata about a
    definition, not an instance read; nothing for the PDP to check per-row).
    Exists so mandatory descriptions are actually queryable, not just
    inert dict values inside `actions.py`.
    """
    return [{"name": name, **definition} for name, definition in actions.ACTION_DEFINITIONS.items()]


@app.get("/actions/{name}")
async def get_action(name: str, principal: Principal = Depends(current_principal)) -> dict:
    definition = actions.ACTION_DEFINITIONS.get(name)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"unknown Action: {name}")
    return {"name": name, **definition}


@app.get("/relation-types")
async def list_relation_types(principal: Principal = Depends(current_principal)) -> list[dict]:
    """Same auth-only convention as `/ontology/{name}` — metadata, not
    instance data, so nothing for the PDP to check per-row.
    """
    return await ontology.list_relation_types(app.state.pool, principal.tenant_id)


@app.get("/relation-types/{name}")
async def get_relation_type(name: str, principal: Principal = Depends(current_principal)) -> dict:
    urn = ontology.relation_type_urn(principal.tenant_id, WORKSPACE_ID, name)
    relation_type = await ontology.get_relation_type(app.state.pool, urn)
    if relation_type is None:
        raise HTTPException(status_code=404, detail=f"unknown RelationType: {name}")
    return relation_type


@app.post("/relation-types", status_code=201)
async def create_relation_type(request: RelationTypeRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Registering a new RelationType is an ontology governance action, not
    a data read/write — gated on the workspace's own `approve` permission
    (admin-only, the same tier that decides high-risk Actions), not
    `_authorize_object_type` (there's no single ObjectType this belongs to;
    it connects two).
    """
    decision = await app.state.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=ontology.workspace_urn(principal.tenant_id, WORKSPACE_ID),
        permission="approve",
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)

    urn = ontology.relation_type_urn(principal.tenant_id, WORKSPACE_ID, request.name)
    if await ontology.get_relation_type(app.state.pool, urn) is not None:
        raise HTTPException(status_code=409, detail=f"RelationType already exists: {request.name}")

    try:
        return await ontology.create_relation_type(
            app.state.pool,
            tenant_id=principal.tenant_id,
            workspace_id=WORKSPACE_ID,
            name=request.name,
            source_object_type=request.source_object_type,
            target_object_type=request.target_object_type,
            source_property=request.source_property,
            cardinality=request.cardinality,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/execute")
async def execute_plan(request: ExecutionRequest, principal: Principal = Depends(current_principal)) -> dict:
    """ExecutionPlan/Adapter abstraction — deliberately minimal (two
    operators, one adapter; see `execution.py`'s module docstring for why).
    Goes through the same PDP path as every other read. Repeat requests
    for the same plan against the same DatasetVersion are served from
    cache, never re-executed — `cached` in the response makes that
    directly observable.
    """
    object_type_urn = OBJECT_TYPE_URNS.get(request.object_type)
    if object_type_urn is None:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {request.object_type}")
    await _authorize_object_type(principal, object_type_urn, "read")

    object_type = await ontology.get_object_type(app.state.pool, object_type_urn)
    if object_type is None:
        raise HTTPException(status_code=500, detail=f"ObjectType {object_type_urn} is not catalogued")
    property_mapping = object_type["property_mapping"]

    try:
        return await execution.get_or_execute(
            app.state.pool,
            ICEBERG_CONFIG,
            tenant_id=principal.tenant_id,
            workspace_id=WORKSPACE_ID,
            object_type_name=request.object_type,
            object_type_urn=object_type_urn,
            property_mapping=property_mapping,
            filter_property=request.filter_property,
            filter_value=request.filter_value,
            operation=request.operation,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/execute/{plan_hash}/replay")
async def replay_plan(plan_hash: str, principal: Principal = Depends(current_principal)) -> dict:
    """Replay a previously-run plan against its pinned historical snapshot and
    report whether it reproduced bit-for-bit. Gated by the same
    `_authorize_object_type` read-check as `/execute`, resolved from the
    stored plan's own `object_type` field — a replay is still a read of
    that ObjectType's data, just of an older version of it.
    """
    row = await app.state.pool.fetchrow("SELECT plan FROM execution_run WHERE plan_hash = $1", plan_hash)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no execution_run found for plan_hash {plan_hash!r}")
    object_type_name = json.loads(row["plan"])["object_type"]
    object_type_urn = OBJECT_TYPE_URNS[object_type_name]
    await _authorize_object_type(principal, object_type_urn, "read")

    try:
        return await execution.replay(app.state.pool, ICEBERG_CONFIG, plan_hash=plan_hash)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/search")
async def unified_search(q: str, principal: Principal = Depends(current_principal)) -> dict:
    """Entitlement-token-filtered search, no post-filtering. See
    `search.py`'s module docstring for the exact ReBAC/ABAC split.
    The ReBAC half is checked here, once, before
    any query reaches OpenSearch — the same workspace `read` permission
    every other read ultimately reduces to, checked directly against the
    `workspace` resource since a search spans every ObjectType at once
    (there's no single object_type_urn to check `_authorize_object_type`
    against). ABAC's per-document narrowing happens *inside* the
    OpenSearch query itself via entitlement tokens, not here.
    """
    decision = await app.state.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=ontology.workspace_urn(principal.tenant_id, WORKSPACE_ID),
        permission="read",
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)

    result = await search.search(OPENSEARCH_URL, OPENSEARCH_PASSWORD, principal=principal, query_text=q)
    # Anonymized query log: tenant + query text + result count only,
    # never the principal who asked. See query_log.py's module docstring.
    await query_log.record_query(app.state.pool, principal.tenant_id, q, result["total"])
    return result


@app.get("/objects/{object_type}/export")
async def export_objects(
    object_type: str, format: str = "json", principal: Principal = Depends(current_principal)
) -> Response:
    """Export-format plugin endpoint — `format=json`
    is always available (the same data every list endpoint already
    returns); any other value must be a registered, active export-format
    plugin. Reads through the identical `_resolve_many` every list
    endpoint uses — confidential property masking included — export is a serialization
    concern layered on top, never a second read path. Registered *before*
    the six type-specific `/objects/{Type}/{id}` routes below: Starlette
    matches routes in registration order, and `/objects/Customer/export`
    would otherwise be captured by `/objects/Customer/{customer_id}`
    (with `customer_id="export"`) first — a real routing conflict caught
    by testing this live, not assumed away.
    """
    if object_type not in OBJECT_TYPE_URNS:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType {object_type!r}")
    await _authorize_object_type(principal, OBJECT_TYPE_URNS[object_type], "read")
    rows = await _resolve_many(object_type, principal.tenant_id, FETCH_FNS[object_type], principal=principal)

    if format == "json":
        return JSONResponse(content=jsonable_encoder(rows))

    found = await export_format_registry.find_active_format(app.state.pool, format)
    if found is None:
        raise HTTPException(status_code=400, detail=f"unknown export format {format!r}")
    plugin, content_type = found
    body = plugin.serialize(jsonable_encoder(rows))
    return Response(content=body, media_type=content_type)


@app.get("/objects/Customer")
async def list_customers(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_object_type(principal, CUSTOMER_OBJECT_TYPE_URN, "read")
    rows = await _resolve_many("Customer", principal.tenant_id, resolver.fetch_customers, principal=principal)
    return await _merge_action_overlays(rows)


@app.get("/objects/Customer/{customer_id}")
async def get_customer(
    customer_id: int, as_of: Optional[datetime] = None, principal: Principal = Depends(current_principal)
) -> dict:
    await _authorize_object_type(principal, CUSTOMER_OBJECT_TYPE_URN, "read")
    row = await _resolve_one(
        "Customer", principal.tenant_id, customer_id, resolver.fetch_customers, "customer_id",
        as_of=as_of, principal=principal,
    )
    if row is None:
        detail = f"Customer/{customer_id} not found"
        if as_of is not None:
            detail += f" as of {as_of.isoformat()} (no history recorded yet at that time)"
        raise HTTPException(status_code=404, detail=detail)
    # Historical read reports the object's own state as of that time —
    # applying *today's* action overlays (credit hold/account closed) to a
    # past snapshot would mix two different points in time into one answer.
    if as_of is not None:
        return row
    return (await _merge_action_overlays([row]))[0]


async def _invoke_customer_action(
    action_name: str, customer_id: int, principal: Principal, reason: str, ttl_seconds: Optional[int] = None
) -> dict:
    """The one entry point every Customer Action's endpoint calls — same
    permission check, same 404 handling, same PDP path. Whether it applies
    immediately or waits for approval is decided entirely inside
    `actions.request_action`, driven by the Action's `risk_level`, not by
    which endpoint got hit.
    """
    definition = actions.ACTION_DEFINITIONS[action_name]
    await _authorize_object_type(principal, CUSTOMER_OBJECT_TYPE_URN, definition["required_permission"])
    if not await _resolve_one("Customer", principal.tenant_id, customer_id, resolver.fetch_customers, "customer_id", principal=principal):
        raise HTTPException(status_code=404, detail=f"Customer/{customer_id} not found")
    return await actions.request_action(
        app.state.pool,
        action_name=action_name,
        tenant_id=principal.tenant_id,
        workspace_id=WORKSPACE_ID,
        customer_id=customer_id,
        principal=principal,
        reason=reason,
        ttl_seconds=ttl_seconds,
    )


@app.post("/objects/Customer/{customer_id}/actions/putOnCreditHold")
async def put_customer_on_credit_hold(
    customer_id: int, request: ActionRequest, principal: Principal = Depends(current_principal)
) -> dict:
    return await _invoke_customer_action(
        "Customer.putOnCreditHold", customer_id, principal, request.reason, request.ttl_seconds
    )


@app.post("/objects/Customer/{customer_id}/actions/closeAccount")
async def close_customer_account(
    customer_id: int, request: ActionRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """High risk (deletion-class): this only ever returns
    `pending_approval`. The mutation happens in `approve_approval` below,
    gated by the `approve` permission (workspace admin only).
    """
    return await _invoke_customer_action(
        "Customer.closeAccount", customer_id, principal, request.reason, request.ttl_seconds
    )


def _require_workflow_engine(principal: Principal) -> None:
    """Same shape as Connectivity's own `_require_saga_orchestrator` — the
    internal compensation endpoint below reverts Step 1 of a saga,
    and must never be reachable by an ordinary authenticated
    caller, only by Automation's Workflow Engine.
    """
    expected_urn = build_urn(TENANT_ID, "global", "service-account", actions.WORKFLOW_ENGINE_URN_NAME)
    if principal.type != "service_account" or principal.urn != expected_urn:
        raise HTTPException(
            status_code=403,
            detail="compensate is restricted to Automation's Workflow Engine — it is not a client-facing endpoint",
        )


@app.post("/internal/approvals/{approval_id}/compensate")
async def compensate_approval(
    approval_id: int, request: CompensationRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Called by Automation's Workflow Engine when its own Step 2 (the
    external write) fails — Knowledge reverts Step 1 of the saga itself,
    since Automation can't reach this service's own tables
    directly. See `actions.compensate_from_workflow_engine`.
    """
    _require_workflow_engine(principal)
    try:
        return await actions.compensate_from_workflow_engine(
            app.state.pool, approval_id=approval_id, workspace_id=WORKSPACE_ID, error=request.error
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/approvals/{approval_id}/approve")
async def approve_approval(
    approval_id: int, request: ApprovalDecisionRequest, principal: Principal = Depends(current_principal)
) -> dict:
    approval = await actions.get_approval(app.state.pool, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"approval {approval_id} not found")
    await _authorize_object_type(principal, _approval_object_type_urn(approval), "approve")
    try:
        return await actions.approve_action(
            app.state.pool,
            approval_id=approval_id,
            workspace_id=WORKSPACE_ID,
            decider=principal,
            note=request.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/approvals/{approval_id}/reject")
async def reject_approval(
    approval_id: int, request: ApprovalDecisionRequest, principal: Principal = Depends(current_principal)
) -> dict:
    approval = await actions.get_approval(app.state.pool, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"approval {approval_id} not found")
    await _authorize_object_type(principal, _approval_object_type_urn(approval), "approve")
    try:
        return await actions.reject_action(
            app.state.pool, approval_id=approval_id, workspace_id=WORKSPACE_ID, decider=principal, note=request.note
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/approvals/{approval_id}")
async def get_approval_by_id(approval_id: int, principal: Principal = Depends(current_principal)) -> dict:
    approval = await actions.get_approval(app.state.pool, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"approval {approval_id} not found")
    await _authorize_object_type(principal, _approval_object_type_urn(approval), "read")
    return approval


@app.get("/approvals")
async def list_pending_approvals(
    status: Optional[str] = None, principal: Principal = Depends(current_principal)
) -> list[dict]:
    await _authorize_object_type(principal, CUSTOMER_OBJECT_TYPE_URN, "read")
    return await actions.list_approvals(app.state.pool, principal.tenant_id, status=status)


@app.get("/objects/Customer/{customer_id}/orders")
async def get_customer_orders(customer_id: int, principal: Principal = Depends(current_principal)) -> list[dict]:
    """Relation traversal (1 to 3 hops) over
    `ontology.RELATION_TYPES` — authorized against what's actually being
    returned (Order), not against Customer.
    """
    await _authorize_object_type(principal, ORDER_OBJECT_TYPE_URN, "read")
    if not await _resolve_one("Customer", principal.tenant_id, customer_id, resolver.fetch_customers, "customer_id", principal=principal):
        raise HTTPException(status_code=404, detail=f"Customer/{customer_id} not found")
    return await _resolve_many(
        "Order", principal.tenant_id, resolver.fetch_orders, principal=principal,
        filter_column="customer_id", filter_kwarg="customer_id", filter_value=customer_id,
    )


@app.get("/objects/Customer/{customer_id}/tickets")
async def get_customer_tickets(customer_id: int, principal: Principal = Depends(current_principal)) -> list[dict]:
    """Second relation traversal, `SupportTicket.customer` — same pattern,
    a structurally different source (MongoDB) underneath.
    """
    await _authorize_object_type(principal, SUPPORT_TICKET_OBJECT_TYPE_URN, "read")
    if not await _resolve_one("Customer", principal.tenant_id, customer_id, resolver.fetch_customers, "customer_id", principal=principal):
        raise HTTPException(status_code=404, detail=f"Customer/{customer_id} not found")
    return await _resolve_many(
        "SupportTicket", principal.tenant_id, resolver.fetch_support_tickets, principal=principal,
        filter_column="customer_id", filter_kwarg="customer_id", filter_value=customer_id,
    )


@app.get("/objects/Order")
async def list_orders(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_object_type(principal, ORDER_OBJECT_TYPE_URN, "read")
    return await _resolve_many("Order", principal.tenant_id, resolver.fetch_orders, principal=principal)


@app.get("/objects/Order/{order_id}")
async def get_order(
    order_id: int, as_of: Optional[datetime] = None, principal: Principal = Depends(current_principal)
) -> dict:
    await _authorize_object_type(principal, ORDER_OBJECT_TYPE_URN, "read")
    row = await _resolve_one("Order", principal.tenant_id, order_id, resolver.fetch_orders, "order_id", as_of=as_of, principal=principal)
    if row is None:
        detail = f"Order/{order_id} not found"
        if as_of is not None:
            detail += f" as of {as_of.isoformat()} (no history recorded yet at that time)"
        raise HTTPException(status_code=404, detail=detail)
    return row


@app.get("/objects/Order/{order_id}/reviews")
async def get_order_reviews(order_id: int, principal: Principal = Depends(current_principal)) -> list[dict]:
    """Third relation traversal, `ProductReview.order` — and the first one
    that doesn't start at Customer: the graph now chains two hops
    (Customer -> Order -> ProductReview), not just a one-level fan-out.
    """
    await _authorize_object_type(principal, PRODUCT_REVIEW_OBJECT_TYPE_URN, "read")
    if not await _resolve_one("Order", principal.tenant_id, order_id, resolver.fetch_orders, "order_id", principal=principal):
        raise HTTPException(status_code=404, detail=f"Order/{order_id} not found")
    return await _resolve_many(
        "ProductReview", principal.tenant_id, resolver.fetch_reviews, principal=principal,
        filter_column="order_id", filter_kwarg="order_id", filter_value=order_id,
    )


@app.get("/objects/ProductReview")
async def list_reviews(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_object_type(principal, PRODUCT_REVIEW_OBJECT_TYPE_URN, "read")
    return await _resolve_many("ProductReview", principal.tenant_id, resolver.fetch_reviews, principal=principal)


@app.get("/objects/ProductReview/{review_id}")
async def get_review(review_id: int, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_object_type(principal, PRODUCT_REVIEW_OBJECT_TYPE_URN, "read")
    row = await _resolve_one("ProductReview", principal.tenant_id, review_id, resolver.fetch_reviews, "review_id", principal=principal)
    if row is None:
        raise HTTPException(status_code=404, detail=f"ProductReview/{review_id} not found")
    return row


@app.get("/objects/SupportTicket")
async def list_support_tickets(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_object_type(principal, SUPPORT_TICKET_OBJECT_TYPE_URN, "read")
    return await _resolve_many("SupportTicket", principal.tenant_id, resolver.fetch_support_tickets, principal=principal)


@app.get("/objects/SupportTicket/{ticket_id}")
async def get_support_ticket(ticket_id: int, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_object_type(principal, SUPPORT_TICKET_OBJECT_TYPE_URN, "read")
    row = await _resolve_one("SupportTicket", principal.tenant_id, ticket_id, resolver.fetch_support_tickets, "ticket_id", principal=principal)
    if row is None:
        raise HTTPException(status_code=404, detail=f"SupportTicket/{ticket_id} not found")
    return row


@app.get("/objects/Supplier")
async def list_suppliers(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_object_type(principal, SUPPLIER_OBJECT_TYPE_URN, "read")
    return await _resolve_many("Supplier", principal.tenant_id, resolver.fetch_suppliers, principal=principal)


@app.get("/objects/Supplier/{supplier_id}")
async def get_supplier(supplier_id: int, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_object_type(principal, SUPPLIER_OBJECT_TYPE_URN, "read")
    row = await _resolve_one("Supplier", principal.tenant_id, supplier_id, resolver.fetch_suppliers, "supplier_id", principal=principal)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Supplier/{supplier_id} not found")
    return row


@app.get("/objects/InventoryLevel")
async def list_inventory_levels(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_object_type(principal, INVENTORY_LEVEL_OBJECT_TYPE_URN, "read")
    return await _resolve_many("InventoryLevel", principal.tenant_id, resolver.fetch_inventory_levels, principal=principal)


@app.get("/objects/InventoryLevel/{sku}")
async def get_inventory_level(sku: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_object_type(principal, INVENTORY_LEVEL_OBJECT_TYPE_URN, "read")
    row = await _resolve_one("InventoryLevel", principal.tenant_id, sku, resolver.fetch_inventory_levels, "sku", principal=principal)
    if row is None:
        raise HTTPException(status_code=404, detail=f"InventoryLevel/{sku} not found")
    return row


class RegisterExecutionAdapterPluginRequest(BaseModel):
    entry_point: str


@app.post("/execution-adapter-plugins")
async def register_execution_adapter_plugin(
    body: RegisterExecutionAdapterPluginRequest, principal: Principal = Depends(current_principal)
) -> dict:
    try:
        return await execution_adapter_registry.register_execution_adapter_plugin(
            app.state.pool, entry_point=body.entry_point
        )
    except execution_adapter_registry.PluginConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/execution-adapter-plugins/{name}")
async def get_execution_adapter_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    registration = await execution_adapter_registry.get_execution_adapter_registration(app.state.pool, name)
    if registration is None:
        raise HTTPException(status_code=404, detail=f"no execution adapter plugin registered as {name!r}")
    return registration


@app.post("/execution-adapter-plugins/{name}/disable")
async def disable_execution_adapter_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    registration = await execution_adapter_registry.get_execution_adapter_registration(app.state.pool, name)
    if registration is None:
        raise HTTPException(status_code=404, detail=f"no execution adapter plugin registered as {name!r}")
    return await execution_adapter_registry.set_execution_adapter_status(app.state.pool, name, "disabled")


@app.post("/execution-adapter-plugins/{name}/enable")
async def enable_execution_adapter_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    registration = await execution_adapter_registry.get_execution_adapter_registration(app.state.pool, name)
    if registration is None:
        raise HTTPException(status_code=404, detail=f"no execution adapter plugin registered as {name!r}")
    return await execution_adapter_registry.set_execution_adapter_status(app.state.pool, name, "active")


class RegisterExportFormatPluginRequest(BaseModel):
    entry_point: str


@app.post("/export-format-plugins")
async def register_export_format_plugin(
    body: RegisterExportFormatPluginRequest, principal: Principal = Depends(current_principal)
) -> dict:
    try:
        return await export_format_registry.register_export_format_plugin(app.state.pool, entry_point=body.entry_point)
    except export_format_registry.PluginConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/export-format-plugins/{name}")
async def get_export_format_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    registration = await export_format_registry.get_export_format_registration(app.state.pool, name)
    if registration is None:
        raise HTTPException(status_code=404, detail=f"no export format plugin registered as {name!r}")
    return registration


@app.post("/export-format-plugins/{name}/disable")
async def disable_export_format_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    registration = await export_format_registry.get_export_format_registration(app.state.pool, name)
    if registration is None:
        raise HTTPException(status_code=404, detail=f"no export format plugin registered as {name!r}")
    return await export_format_registry.set_export_format_status(app.state.pool, name, "disabled")


@app.post("/export-format-plugins/{name}/enable")
async def enable_export_format_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    registration = await export_format_registry.get_export_format_registration(app.state.pool, name)
    if registration is None:
        raise HTTPException(status_code=404, detail=f"no export format plugin registered as {name!r}")
    return await export_format_registry.set_export_format_status(app.state.pool, name, "active")


@app.get("/lineage/{urn:path}")
async def get_lineage(urn: str, principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_object_type(principal, CUSTOMER_OBJECT_TYPE_URN, "read")  # all lineage here traces back to Customer or Order
    return await lineage.edges_touching(app.state.pool, principal.tenant_id, urn)
