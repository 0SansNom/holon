"""Identity Platform — Tenant, Workspace, and Principal management.

Owns Tenant, Workspace and Principal, issues bearer tokens, and loads the
shared SpiceDB schema plus the relationships it owns (tenant
membership, workspace access). The actual authorization decisions are
made where the resources being read live — see `holon_common.authz`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import uuid
from contextlib import asynccontextmanager

import asyncpg
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from holon_common import (
    EventActor,
    EventEnvelope,
    EventProducer,
    PermissionClient,
    Principal,
    build_urn,
    configure_json_logging,
    create_pool,
    instrument_cors,
    instrument_metrics,
    instrument_tracing,
    issue_token,
    make_principal_dependency,
    outbox,
    retry_with_backoff,
)

from .seed import VALID_WORKSPACE_RELATIONS, ensure_authz_seeded, ensure_seeded, workspace_urn

SERVICE_NAME = "identity-platform"
configure_json_logging(SERVICE_NAME)
logger = logging.getLogger("identity")

TENANT_ID = os.environ["HOLON_TENANT_ID"]
WORKSPACE_ID = os.environ["HOLON_WORKSPACE_ID"]
JWT_SECRET = os.environ["HOLON_JWT_SECRET"]
DB_URL = os.environ["HOLON_DB_URL"]
SPICEDB_URL = os.environ["HOLON_SPICEDB_URL"]
SPICEDB_PRESHARED_KEY = os.environ["HOLON_SPICEDB_PRESHARED_KEY"]
SPICEDB_SCHEMA_PATH = os.environ["HOLON_SPICEDB_SCHEMA_PATH"]
OPA_URL = os.environ["HOLON_OPA_URL"]
KAFKA_BOOTSTRAP = os.environ["HOLON_KAFKA_BOOTSTRAP"]
OTLP_ENDPOINT = os.environ["HOLON_OTLP_ENDPOINT"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool(DB_URL)
    async with app.state.pool.acquire() as conn:
        await ensure_seeded(conn, TENANT_ID, WORKSPACE_ID)
        await outbox.ensure_schema(conn)

    app.state.authz = PermissionClient(SPICEDB_URL, SPICEDB_PRESHARED_KEY, OPA_URL)
    await retry_with_backoff(
        lambda: ensure_authz_seeded(app.state.authz, SPICEDB_SCHEMA_PATH, TENANT_ID, WORKSPACE_ID),
        what="identity authz seed",
    )

    app.state.producer = EventProducer(KAFKA_BOOTSTRAP)
    await app.state.producer.start()
    relay_task = asyncio.create_task(outbox.relay_forever(app.state.pool, app.state.producer, dlq_producer=app.state.producer))

    yield

    relay_task.cancel()
    await app.state.producer.stop()
    await app.state.authz.aclose()
    await app.state.pool.close()


app = FastAPI(title="Holon — Identity Platform", lifespan=lifespan)
instrument_cors(app)
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
current_principal = make_principal_dependency(JWT_SECRET)


class TokenRequest(BaseModel):
    principal_urn: str
    client_secret: str


class AccessRequest(BaseModel):
    relation: str


def _principal_from_row(row: asyncpg.Record) -> Principal:
    fields = {k: v for k, v in dict(row).items() if k != "client_secret"}
    return Principal(**fields)


async def _fetch_principal(pool: asyncpg.Pool, urn: str) -> Principal | None:
    row = await pool.fetchrow("SELECT * FROM principal WHERE urn = $1", urn)
    return _principal_from_row(row) if row else None


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


@app.get("/principals", response_model=list[Principal])
async def list_principals() -> list[Principal]:
    rows = await app.state.pool.fetch("SELECT * FROM principal ORDER BY urn")
    return [_principal_from_row(row) for row in rows]


@app.post("/token")
async def mint_token(request: TokenRequest) -> dict:
    """`client_secret` verifies principal identity prior to issuing bearer tokens.
    """
    row = await app.state.pool.fetchrow("SELECT * FROM principal WHERE urn = $1", request.principal_urn)
    if row is None or not secrets.compare_digest(row["client_secret"], request.client_secret):
        raise HTTPException(status_code=401, detail="invalid principal_urn or client_secret")
    principal = _principal_from_row(row)
    return {"access_token": issue_token(principal, JWT_SECRET), "token_type": "bearer"}


@app.get("/whoami", response_model=Principal)
async def whoami(principal: Principal = Depends(current_principal)) -> Principal:
    return principal


async def _authorize_governance_action(principal: Principal) -> None:
    """Granting/revoking workspace access is a governance action, gated on
    the workspace's own `approve` permission.
    """
    decision = await app.state.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=workspace_urn(TENANT_ID, WORKSPACE_ID),
        permission="approve",
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)


def _validate_relation(relation: str) -> None:
    if relation not in VALID_WORKSPACE_RELATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"invalid relation: {relation!r} (must be one of {sorted(VALID_WORKSPACE_RELATIONS)})",
        )


@app.post("/principals/{principal_urn:path}/access/grant")
async def grant_access(
    principal_urn: str, request: AccessRequest, principal: Principal = Depends(current_principal)
) -> dict:
    await _authorize_governance_action(principal)
    _validate_relation(request.relation)
    await app.state.authz.write_relationship(
        resource_type="workspace",
        resource_urn=workspace_urn(TENANT_ID, WORKSPACE_ID),
        relation=request.relation,
        subject_urn=principal_urn,
    )
    return {"status": "granted", "principalUrn": principal_urn, "relation": request.relation}


@app.post("/principals/{principal_urn:path}/access/revoke")
async def revoke_access(
    principal_urn: str, request: AccessRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """`delete_relationship` is the authoritative mutation — access is
    already denied the moment SpiceDB confirms it.
    """
    await _authorize_governance_action(principal)
    _validate_relation(request.relation)

    w_urn = workspace_urn(TENANT_ID, WORKSPACE_ID)
    await app.state.authz.delete_relationship(
        resource_type="workspace",
        resource_urn=w_urn,
        relation=request.relation,
        subject_urn=principal_urn,
    )

    event_id = uuid.uuid4().hex
    event = EventEnvelope(
        event_id=event_id,
        event_type="identity.permission.revoked",
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        aggregate_type="Principal",
        aggregate_id=principal_urn,
        correlation_id=event_id,
        partition_key=f"{TENANT_ID}/{principal_urn}",
        producer="identity-platform@0.1.0",
        actor=EventActor(type=principal.type, urn=principal.urn, on_behalf_of=principal.on_behalf_of),
        payload={
            "principal_urn": principal_urn,
            "resource_type": "workspace",
            "resource_urn": w_urn,
            "relation": request.relation,
        },
    )
    async with app.state.pool.acquire() as conn:
        async with conn.transaction():
            await outbox.enqueue(conn, event)

    return {"status": "revoked", "principalUrn": principal_urn, "relation": request.relation}
