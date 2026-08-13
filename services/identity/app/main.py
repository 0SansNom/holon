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
from pathlib import Path

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from holon_common import (
    EventActor,
    EventEnvelope,
    EventProducer,
    PermissionClient,
    Principal,
    active_jwt,
    assert_production_posture,
    build_urn,
    clear_session_cookie,
    configure_json_logging,
    create_pool,
    install_error_handlers,
    instrument_cors,
    instrument_metrics,
    instrument_tracing,
    issue_token,
    make_principal_dependency,
    outbox,
    retry_with_backoff,
    run_migrations,
    set_session_cookie,
)

from . import oidc as oidc_client
from .seed import (
    VALID_PROJECT_RELATIONS,
    VALID_WORKSPACE_RELATIONS,
    allow_dev_login,
    create_project,
    create_tenant,
    create_workspace,
    ensure_instance_bootstrap,
    ensure_schema,
    get_project,
    get_tenant,
    get_workspace,
    insert_principal,
    list_projects,
    list_tenants,
    list_workspaces,
    project_urn,
    set_principal_status,
    set_tenant_status,
    set_workspace_status,
    tenant_urn,
    workspace_urn,
)

SERVICE_NAME = "identity-platform"
configure_json_logging(SERVICE_NAME)
logger = logging.getLogger("identity")

TENANT_ID = os.environ["HOLON_TENANT_ID"]
WORKSPACE_ID = os.environ["HOLON_WORKSPACE_ID"]
JWT_SECRET, JWT_ACTIVE_KID, JWT_SECRETS = active_jwt()
DB_URL = os.environ["HOLON_DB_URL"]
SPICEDB_URL = os.environ["HOLON_SPICEDB_URL"]
SPICEDB_PRESHARED_KEY = os.environ["HOLON_SPICEDB_PRESHARED_KEY"]
SPICEDB_SCHEMA_PATH = os.environ["HOLON_SPICEDB_SCHEMA_PATH"]
OPA_URL = os.environ["HOLON_OPA_URL"]
KAFKA_BOOTSTRAP = os.environ["HOLON_KAFKA_BOOTSTRAP"]
OTLP_ENDPOINT = os.environ.get("HOLON_OTLP_ENDPOINT", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_production_posture(service_name=SERVICE_NAME)
    app.state.pool = await create_pool(DB_URL)
    async with app.state.pool.acquire() as conn:
        await ensure_schema(conn)
        await outbox.ensure_schema(conn)
    await run_migrations(app.state.pool, Path(__file__).parent / "migrations")

    app.state.authz = PermissionClient(SPICEDB_URL, SPICEDB_PRESHARED_KEY, OPA_URL)
    await retry_with_backoff(
        lambda: app.state.authz.write_schema(Path(SPICEDB_SCHEMA_PATH).read_text()),
        what="identity authz schema",
    )
    await retry_with_backoff(
        lambda: ensure_instance_bootstrap(
            app.state.pool, app.state.authz, tenant_id=TENANT_ID, workspace_id=WORKSPACE_ID
        ),
        what="identity empty-instance bootstrap",
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
install_error_handlers(app, service_name=SERVICE_NAME)
current_principal = make_principal_dependency(JWT_SECRET, secrets=JWT_SECRETS)


def _issue(principal: Principal, *, ttl_seconds: int | None = None) -> str:
    kwargs: dict = {"kid": JWT_ACTIVE_KID, "secrets": JWT_SECRETS, "allow_user": True}
    if ttl_seconds is not None:
        kwargs["ttl_seconds"] = ttl_seconds
    return issue_token(principal, JWT_SECRET, **kwargs)


class TokenRequest(BaseModel):
    principal_urn: str
    client_secret: str


class AccessRequest(BaseModel):
    relation: str
    # When omitted: bootstrap workspace for the bootstrap tenant, otherwise
    # the first workspace in the target tenant the caller can approve.
    workspace_id: str | None = None


def _principal_from_row(row: asyncpg.Record) -> Principal:
    fields = {
        k: v
        for k, v in dict(row).items()
        if k not in ("client_secret", "status", "oidc_sub")
    }
    return Principal(**fields)


async def _fetch_principal(pool: asyncpg.Pool, urn: str) -> Principal | None:
    row = await pool.fetchrow("SELECT * FROM principal WHERE urn = $1", urn)
    return _principal_from_row(row) if row else None


async def _require_active_principal_row(urn: str) -> asyncpg.Record:
    row = await app.state.pool.fetchrow("SELECT * FROM principal WHERE urn = $1", urn)
    if row is None:
        raise HTTPException(status_code=401, detail="invalid principal_urn or client_secret")
    if row["status"] != "active":
        raise HTTPException(status_code=403, detail="principal is disabled")
    return row


def _reject_dev_secret_if_disabled(client_secret: str) -> None:
    if allow_dev_login():
        return
    if isinstance(client_secret, str) and client_secret.endswith("-dev-secret"):
        raise HTTPException(
            status_code=403,
            detail="dev client_secret login disabled (set HOLON_ALLOW_DEV_LOGIN=true for local demo)",
        )


async def _require_grant_target(urn: str, *, tenant_id: str) -> Principal:
    target = await _fetch_principal(app.state.pool, urn)
    if target is None:
        raise HTTPException(status_code=404, detail=f"unknown principal: {urn}")
    if target.tenant_id != tenant_id:
        raise HTTPException(status_code=400, detail="principal belongs to another tenant")
    return target


async def _authorize_bootstrap_governance(principal: Principal) -> None:
    """Creating tenants (filiales) is instance-level: gated on approve of
    the bootstrap workspace (ADR 026)."""
    decision = await app.state.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=workspace_urn(TENANT_ID, WORKSPACE_ID),
        permission="approve",
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)


async def _authorize_workspace_governance(principal: Principal, tenant_id: str, workspace_id: str) -> str:
    ws = await get_workspace(app.state.pool, workspace_id)
    if ws is None or ws["tenant_id"] != tenant_id:
        raise HTTPException(status_code=404, detail=f"unknown workspace: {workspace_id}")
    if ws["status"] != "active":
        raise HTTPException(status_code=400, detail="workspace is disabled")
    w_urn = workspace_urn(tenant_id, workspace_id)
    decision = await app.state.authz.authorize(
        principal, resource_type="workspace", resource_urn=w_urn, permission="approve"
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)
    return w_urn


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
async def list_principals(principal: Principal = Depends(current_principal)) -> list[Principal]:
    """Authenticated-only: principals in the caller's tenant only
    (multi-org isolation — ADR 026)."""
    rows = await app.state.pool.fetch(
        "SELECT * FROM principal WHERE tenant_id = $1 ORDER BY urn", principal.tenant_id
    )
    return [_principal_from_row(row) for row in rows]


@app.post("/token")
async def mint_token(request: TokenRequest) -> dict:
    """`client_secret` verifies principal identity prior to issuing bearer tokens.

    Unchanged, on purpose — this is the CLI/script/service-to-service
    path (`HolonClient.token_for`, every test
    fixture, internal service-account minting), none of which have a
    cookie jar. `/login` below is the browser's own path.
    """
    row = await _require_active_principal_row(request.principal_urn)
    _reject_dev_secret_if_disabled(request.client_secret)
    if not secrets.compare_digest(row["client_secret"], request.client_secret):
        raise HTTPException(status_code=401, detail="invalid principal_urn or client_secret")
    principal = _principal_from_row(row)
    return {"access_token": _issue(principal), "token_type": "bearer"}


@app.post("/login")
async def login(request: TokenRequest, response: Response) -> dict:
    """The browser's sign-in path — same credential check as `/token`,
    but the issued JWT is set as an HttpOnly cookie instead of ever
    appearing in a response body, so it's never reachable from page JS
    (defeats XSS-based token theft, `localStorage`'s weakness — see
    `holon_common.auth`'s `set_session_cookie`/module docstring).
    """
    row = await _require_active_principal_row(request.principal_urn)
    _reject_dev_secret_if_disabled(request.client_secret)
    if not secrets.compare_digest(row["client_secret"], request.client_secret):
        raise HTTPException(status_code=401, detail="invalid principal_urn or client_secret")
    principal = _principal_from_row(row)
    set_session_cookie(response, _issue(principal))
    return {"status": "ok"}


@app.post("/logout")
async def logout(response: Response) -> dict:
    clear_session_cookie(response)
    return {"status": "ok"}


@app.get("/oidc/login")
async def oidc_login() -> dict:
    """Start OIDC authorization-code + PKCE. 404 when HOLON_OIDC_ISSUER unset."""
    if not oidc_client.oidc_enabled():
        raise HTTPException(status_code=404, detail="OIDC is not configured")
    redirect_uri = os.environ.get(
        "HOLON_OIDC_REDIRECT_URI", "http://localhost:8001/oidc/callback"
    )
    return await oidc_client.build_authorize_url(app.state.pool, redirect_uri=redirect_uri)


@app.get("/oidc/callback")
async def oidc_callback(code: str, state: str):
    if not oidc_client.oidc_enabled():
        raise HTTPException(status_code=404, detail="OIDC is not configured")
    try:
        claims = await oidc_client.exchange_code(app.state.pool, code=code, state=state)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"OIDC exchange failed: {exc}") from exc

    sub = str(claims.get("sub") or "")
    if not sub:
        raise HTTPException(status_code=401, detail="OIDC claims missing sub")
    tenant_id = oidc_client.tenant_from_claims(claims, default_tenant=TENANT_ID)
    tenant = await get_tenant(app.state.pool, tenant_id)
    if tenant is None or tenant["status"] != "active":
        raise HTTPException(status_code=403, detail=f"unknown or disabled tenant for OIDC login: {tenant_id}")

    row = await app.state.pool.fetchrow("SELECT * FROM principal WHERE oidc_sub = $1", sub)
    if row is None:
        local_name = oidc_client.local_name_from_claims(claims)
        try:
            created = await insert_principal(
                app.state.pool,
                tenant_id=tenant_id,
                type="user",
                local_name=local_name,
                display_name=oidc_client.display_name_from_claims(claims),
                oidc_sub=sub,
            )
        except asyncpg.UniqueViolationError:
            created = dict(
                await app.state.pool.fetchrow(
                    "SELECT * FROM principal WHERE urn = $1",
                    build_urn(tenant_id, "global", "user", local_name),
                )
            )
            await app.state.pool.execute(
                "UPDATE principal SET oidc_sub = $2 WHERE urn = $1", created["urn"], sub
            )
        await app.state.authz.write_relationship(
            resource_type="tenant",
            resource_urn=tenant_urn(tenant_id),
            relation="member",
            subject_urn=created["urn"],
        )
        row = await app.state.pool.fetchrow("SELECT * FROM principal WHERE urn = $1", created["urn"])
    else:
        # Returning user: keep original DB tenant mapping
        if row["tenant_id"] != tenant_id:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"OIDC tenant claim {tenant_id!r} does not match linked principal "
                    f"tenant {row['tenant_id']!r}; unlink oidc_sub or update the principal"
                ),
            )

    if row["status"] != "active":
        raise HTTPException(status_code=403, detail="principal is disabled")
    principal = _principal_from_row(row)

    # Optional group → workspace relation sync (viewer) within this tenant.
    group_prefix = os.environ.get("HOLON_OIDC_WORKSPACE_GROUP_PREFIX", "workspace:")
    for group in oidc_client.groups_from_claims(claims):
        if not group.startswith(group_prefix):
            continue
        workspace_id = group[len(group_prefix) :]
        ws = await get_workspace(app.state.pool, workspace_id)
        if ws is None or ws["tenant_id"] != principal.tenant_id:
            continue
        await app.state.authz.write_relationship(
            resource_type="workspace",
            resource_urn=workspace_urn(principal.tenant_id, workspace_id),
            relation="viewer",
            subject_urn=principal.urn,
        )

    # Set session cookie on RedirectResponse object
    frontend = os.environ.get("HOLON_OIDC_POST_LOGIN_REDIRECT", "http://localhost:5173/objects")
    redirect = RedirectResponse(url=frontend, status_code=302)
    set_session_cookie(redirect, _issue(principal))
    return redirect


@app.get("/whoami", response_model=Principal)
async def whoami(principal: Principal = Depends(current_principal)) -> Principal:
    return principal


# ---- Multi-org provisioning (ADR 026) ---------------------------------


class CreateTenantRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    display_name: str = Field(min_length=1, max_length=256)


class CreateWorkspaceRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    display_name: str = Field(min_length=1, max_length=256)
    tenant_id: str = Field(min_length=1, max_length=64)
    # Required when the caller is not a member of `tenant_id` (instance
    # admin provisioning a filiale). Must be a principal already in that tenant.
    initial_admin_urn: str | None = None


class CreatePrincipalRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    type: str = Field(pattern=r"^(user|agent|service_account)$")
    local_name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    display_name: str = Field(min_length=1, max_length=256)
    country: str | None = None
    on_behalf_of: str | None = None


class StatusRequest(BaseModel):
    status: str = Field(pattern=r"^(active|disabled)$")


@app.get("/tenants")
async def tenants_list(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_bootstrap_governance(principal)
    return await list_tenants(app.state.pool)


@app.post("/tenants", status_code=201)
async def tenants_create(request: CreateTenantRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Create a filiale tenant row only. No SpiceDB membership for the
    caller — instance admins must not become cross-tenant subjects
    (ADR 026). First same-tenant principal / workspace admin is granted
    via `POST /principals` and `POST /workspaces` (`initial_admin_urn`).
    """
    await _authorize_bootstrap_governance(principal)
    if await get_tenant(app.state.pool, request.tenant_id) is not None:
        raise HTTPException(status_code=409, detail=f"tenant already exists: {request.tenant_id}")
    return await create_tenant(app.state.pool, tenant_id=request.tenant_id, display_name=request.display_name)


@app.post("/tenants/{tenant_id}/status")
async def tenants_set_status(
    tenant_id: str, request: StatusRequest, principal: Principal = Depends(current_principal)
) -> dict:
    await _authorize_bootstrap_governance(principal)
    if await get_tenant(app.state.pool, tenant_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown tenant: {tenant_id}")
    updated = await set_tenant_status(app.state.pool, tenant_id, request.status)
    return updated  # type: ignore[return-value]


@app.get("/workspaces")
async def workspaces_list(
    tenant_id: str | None = None, principal: Principal = Depends(current_principal)
) -> list[dict]:
    # Callers see workspaces in their own tenant unless bootstrap admin lists all.
    try:
        await _authorize_bootstrap_governance(principal)
        return await list_workspaces(app.state.pool, tenant_id)
    except HTTPException:
        return await list_workspaces(app.state.pool, principal.tenant_id)


@app.post("/workspaces", status_code=201)
async def workspaces_create(
    request: CreateWorkspaceRequest, principal: Principal = Depends(current_principal)
) -> dict:
    tenant = await get_tenant(app.state.pool, request.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"unknown tenant: {request.tenant_id}")
    if tenant["status"] != "active":
        raise HTTPException(status_code=400, detail="tenant is disabled")
    # Bootstrap admins may create the first workspace on a new filiale;
    # otherwise require approve on an existing workspace in that tenant.
    existing = await list_workspaces(app.state.pool, request.tenant_id)
    if not existing:
        await _authorize_bootstrap_governance(principal)
    else:
        await _authorize_workspace_governance(principal, request.tenant_id, existing[0]["workspace_id"])
    if await get_workspace(app.state.pool, request.workspace_id) is not None:
        raise HTTPException(status_code=409, detail=f"workspace already exists: {request.workspace_id}")

    # Never grant workspace admin to a principal from another tenant —
    # instance admins nominate a same-tenant `initial_admin_urn`.
    if principal.tenant_id == request.tenant_id:
        admin_urn = principal.urn
    else:
        if not request.initial_admin_urn:
            raise HTTPException(
                status_code=400,
                detail="initial_admin_urn is required when creating a workspace outside your tenant "
                "(create the filiale principal first, then pass their URN)",
            )
        admin = await _fetch_principal(app.state.pool, request.initial_admin_urn)
        if admin is None:
            raise HTTPException(status_code=404, detail=f"unknown principal: {request.initial_admin_urn}")
        if admin.tenant_id != request.tenant_id:
            raise HTTPException(
                status_code=400,
                detail="initial_admin_urn must belong to the workspace's tenant",
            )
        admin_urn = admin.urn

    workspace = await create_workspace(
        app.state.pool,
        tenant_id=request.tenant_id,
        workspace_id=request.workspace_id,
        display_name=request.display_name,
    )
    await app.state.authz.write_relationship(
        resource_type="workspace",
        resource_urn=workspace_urn(request.tenant_id, request.workspace_id),
        relation="parent_tenant",
        subject_type="tenant",
        subject_urn=tenant_urn(request.tenant_id),
    )
    await app.state.authz.write_relationship(
        resource_type="workspace",
        resource_urn=workspace_urn(request.tenant_id, request.workspace_id),
        relation="admin",
        subject_urn=admin_urn,
    )
    return workspace


@app.post("/workspaces/{workspace_id}/status")
async def workspaces_set_status(
    workspace_id: str, request: StatusRequest, principal: Principal = Depends(current_principal)
) -> dict:
    ws = await get_workspace(app.state.pool, workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail=f"unknown workspace: {workspace_id}")
    await _authorize_workspace_governance(principal, ws["tenant_id"], workspace_id)
    updated = await set_workspace_status(app.state.pool, workspace_id, request.status)
    return updated  # type: ignore[return-value]


@app.post("/principals", status_code=201)
async def principals_create(
    request: CreatePrincipalRequest, principal: Principal = Depends(current_principal)
) -> dict:
    tenant = await get_tenant(app.state.pool, request.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"unknown tenant: {request.tenant_id}")
    workspaces = await list_workspaces(app.state.pool, request.tenant_id)
    if not workspaces:
        await _authorize_bootstrap_governance(principal)
    else:
        await _authorize_workspace_governance(principal, request.tenant_id, workspaces[0]["workspace_id"])
    try:
        row = await insert_principal(
            app.state.pool,
            tenant_id=request.tenant_id,
            type=request.type,
            local_name=request.local_name,
            display_name=request.display_name,
            country=request.country,
            on_behalf_of=request.on_behalf_of,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="principal already exists") from exc
    await app.state.authz.write_relationship(
        resource_type="tenant",
        resource_urn=tenant_urn(request.tenant_id),
        relation="member",
        subject_urn=row["urn"],
    )
    # Never return client_secret in list form; include once at create for service accounts.
    return {
        "urn": row["urn"],
        "type": row["type"],
        "tenant_id": row["tenant_id"],
        "display_name": row["display_name"],
        "on_behalf_of": row["on_behalf_of"],
        "country": row["country"],
        "status": row["status"],
        "client_secret": row["client_secret"],
    }


@app.post("/principals/{principal_urn:path}/status")
async def principals_set_status(
    principal_urn: str, request: StatusRequest, principal: Principal = Depends(current_principal)
) -> dict:
    target = await _fetch_principal(app.state.pool, principal_urn)
    if target is None:
        raise HTTPException(status_code=404, detail=f"unknown principal: {principal_urn}")
    workspaces = await list_workspaces(app.state.pool, target.tenant_id)
    if not workspaces:
        await _authorize_bootstrap_governance(principal)
    else:
        await _authorize_workspace_governance(principal, target.tenant_id, workspaces[0]["workspace_id"])
    updated = await set_principal_status(app.state.pool, principal_urn, request.status)
    assert updated is not None
    return {k: updated[k] for k in ("urn", "type", "tenant_id", "display_name", "status")}


async def _resolve_workspace_governance(
    principal: Principal, *, tenant_id: str, workspace_id: str | None
) -> tuple[str, str]:
    """Authorize `approve` on a workspace in `tenant_id`.

    Explicit `workspace_id` wins. Otherwise: bootstrap tenant → env
    WORKSPACE_ID (keeps existing Admin UI / tests); other tenants → first
    workspace the caller can approve.
    """
    if workspace_id:
        await _authorize_workspace_governance(principal, tenant_id, workspace_id)
        return tenant_id, workspace_id
    if tenant_id == TENANT_ID:
        await _authorize_workspace_governance(principal, TENANT_ID, WORKSPACE_ID)
        return TENANT_ID, WORKSPACE_ID
    workspaces = await list_workspaces(app.state.pool, tenant_id)
    if not workspaces:
        raise HTTPException(status_code=400, detail=f"tenant {tenant_id!r} has no workspace")
    last_exc: HTTPException | None = None
    for ws in workspaces:
        try:
            await _authorize_workspace_governance(principal, tenant_id, ws["workspace_id"])
            return tenant_id, ws["workspace_id"]
        except HTTPException as exc:
            if exc.status_code == 403:
                last_exc = exc
                continue
            raise
    raise last_exc or HTTPException(status_code=403, detail="access denied: workspace approve required")


async def _access_listing(resource_type: str, resource_urn: str, valid_relations: set[str]) -> list[dict]:
    """The read side of ReBAC governance: enumerate a resource's direct
    grants. SpiceDB object IDs store URNs with ':' swapped for '_', a
    transform that is not naively reversible, so subject IDs are mapped
    back through the principal table — which also enriches each entry
    with the display fields an admin UI needs. A subject with no
    principal row is an orphaned tuple (grant endpoints never validated
    the target against the principal table); it is reported with null
    display fields rather than silently dropped, so admins can still see
    it — and revoke it, since the raw subject id contains no ':' and is
    therefore round-trip safe as a `principal_urn`.
    """
    relationships = await app.state.authz.read_relationships(resource_type=resource_type, resource_urn=resource_urn)
    rows = await app.state.pool.fetch("SELECT * FROM principal")
    by_object_id = {r["urn"].replace(":", "_"): r for r in rows}

    grants = []
    for rel in relationships:
        relation = rel.get("relation", "")
        subject = rel.get("subject", {}).get("object", {})
        if relation not in valid_relations or subject.get("objectType") != "principal":
            continue  # parent_tenant/parent_workspace edges are hierarchy, not access grants
        subject_id = subject.get("objectId", "")
        row = by_object_id.get(subject_id)
        grants.append(
            {
                "principal_urn": row["urn"] if row else subject_id,
                "display_name": row["display_name"] if row else None,
                "type": row["type"] if row else None,
                "relation": relation,
            }
        )
    return sorted(grants, key=lambda g: (g["principal_urn"], g["relation"]))


def _validate_relation(relation: str) -> None:
    if relation not in VALID_WORKSPACE_RELATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"invalid relation: {relation!r} (must be one of {sorted(VALID_WORKSPACE_RELATIONS)})",
        )


async def _enqueue_permission_event(
    *,
    event_type: str,
    target_principal_urn: str,
    resource_type: str,
    resource_urn: str,
    relation: str,
    actor: Principal,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> None:
    event_id = uuid.uuid4().hex
    tid = tenant_id or actor.tenant_id
    wid = workspace_id or WORKSPACE_ID
    event = EventEnvelope(
        event_id=event_id,
        event_type=event_type,
        tenant_id=tid,
        workspace_id=wid,
        aggregate_type="Principal",
        aggregate_id=target_principal_urn,
        correlation_id=event_id,
        partition_key=f"{tid}/{target_principal_urn}",
        producer="identity-platform@0.1.0",
        actor=EventActor(type=actor.type, urn=actor.urn, on_behalf_of=actor.on_behalf_of),
        payload={
            "principal_urn": target_principal_urn,
            "resource_type": resource_type,
            "resource_urn": resource_urn,
            "relation": relation,
        },
    )
    async with app.state.pool.acquire() as conn:
        async with conn.transaction():
            await outbox.enqueue(conn, event)


@app.post("/principals/{principal_urn:path}/access/grant")
async def grant_access(
    principal_urn: str, request: AccessRequest, principal: Principal = Depends(current_principal)
) -> dict:
    _validate_relation(request.relation)
    target = await _fetch_principal(app.state.pool, principal_urn)
    if target is None:
        raise HTTPException(status_code=404, detail=f"unknown principal: {principal_urn}")
    tid, wid = await _resolve_workspace_governance(
        principal, tenant_id=target.tenant_id, workspace_id=request.workspace_id
    )
    await _require_grant_target(principal_urn, tenant_id=tid)
    w_urn = workspace_urn(tid, wid)
    await app.state.authz.write_relationship(
        resource_type="workspace",
        resource_urn=w_urn,
        relation=request.relation,
        subject_urn=principal_urn,
    )
    await _enqueue_permission_event(
        event_type="identity.permission.granted",
        target_principal_urn=principal_urn,
        resource_type="workspace",
        resource_urn=w_urn,
        relation=request.relation,
        actor=principal,
        tenant_id=tid,
        workspace_id=wid,
    )
    return {"status": "granted", "principalUrn": principal_urn, "relation": request.relation, "workspace_id": wid}


@app.post("/principals/{principal_urn:path}/access/revoke")
async def revoke_access(
    principal_urn: str, request: AccessRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """`delete_relationship` is the authoritative mutation — access is
    already denied the moment SpiceDB confirms it.
    """
    _validate_relation(request.relation)
    target = await _fetch_principal(app.state.pool, principal_urn)
    if target is None:
        raise HTTPException(status_code=404, detail=f"unknown principal: {principal_urn}")
    tid, wid = await _resolve_workspace_governance(
        principal, tenant_id=target.tenant_id, workspace_id=request.workspace_id
    )

    w_urn = workspace_urn(tid, wid)
    await app.state.authz.delete_relationship(
        resource_type="workspace",
        resource_urn=w_urn,
        relation=request.relation,
        subject_urn=principal_urn,
    )

    await _enqueue_permission_event(
        event_type="identity.permission.revoked",
        target_principal_urn=principal_urn,
        resource_type="workspace",
        resource_urn=w_urn,
        relation=request.relation,
        actor=principal,
        tenant_id=tid,
        workspace_id=wid,
    )

    return {"status": "revoked", "principalUrn": principal_urn, "relation": request.relation, "workspace_id": wid}


@app.get("/access")
async def list_workspace_access(
    workspace_id: str | None = None, principal: Principal = Depends(current_principal)
) -> list[dict]:
    """Who currently holds viewer/editor/admin on the workspace. Same
    governance gate as the mutations (`approve`) — the membership list is
    itself sensitive.
    """
    tid, wid = await _resolve_workspace_governance(
        principal, tenant_id=principal.tenant_id, workspace_id=workspace_id
    )
    return await _access_listing("workspace", workspace_urn(tid, wid), VALID_WORKSPACE_RELATIONS)


class CreateProjectRequest(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
        description="URL-safe project name",
    )


@app.post("/projects", status_code=201)
async def create_project_endpoint(request: CreateProjectRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Creating a project is workspace-tier governance (`approve`,
    admin-only) — same tier as granting workspace access itself. Once
    created, day-to-day project membership (`.../access/grant`) can be
    delegated to a project-specific admin, not just workspace admins.
    """
    tid, wid = await _resolve_workspace_governance(
        principal, tenant_id=principal.tenant_id, workspace_id=None
    )
    urn = project_urn(tid, wid, request.name)
    if await get_project(app.state.pool, urn) is not None:
        raise HTTPException(status_code=409, detail=f"project already exists: {request.name}")
    project = await create_project(app.state.pool, tenant_id=tid, workspace_id=wid, name=request.name)
    await app.state.authz.write_relationship(
        resource_type="project",
        resource_urn=urn,
        relation="parent_workspace",
        subject_type="workspace",
        subject_urn=workspace_urn(tid, wid),
    )
    return project


@app.get("/projects")
async def list_projects_endpoint(principal: Principal = Depends(current_principal)) -> list[dict]:
    return await list_projects(app.state.pool, principal.tenant_id)


@app.get("/projects/{name}")
async def get_project_endpoint(name: str, principal: Principal = Depends(current_principal)) -> dict:
    projects = await list_projects(app.state.pool, principal.tenant_id)
    project = next((p for p in projects if p["name"] == name), None)
    if project is None:
        raise HTTPException(status_code=404, detail=f"unknown project: {name}")
    return project


async def _authorize_project_governance(principal: Principal, project_name: str) -> str:
    """Cascading, same as `object_type`'s own SpiceDB permission formula:
    a workspace admin can govern any project (`parent_workspace->approve`),
    *and* a project can have its own directly-granted admin distinct from
    anyone at the workspace tier — not an either/or, a union.
    """
    projects = await list_projects(app.state.pool, principal.tenant_id)
    project = next((p for p in projects if p["name"] == project_name), None)
    if project is None:
        raise HTTPException(status_code=404, detail=f"unknown project: {project_name}")
    urn = project["urn"]
    decision = await app.state.authz.authorize(principal, resource_type="project", resource_urn=urn, permission="approve")
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)
    return urn


def _validate_project_relation(relation: str) -> None:
    if relation not in VALID_PROJECT_RELATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"invalid relation: {relation!r} (must be one of {sorted(VALID_PROJECT_RELATIONS)})",
        )


@app.post("/projects/{name}/principals/{principal_urn:path}/access/grant")
async def grant_project_access(
    name: str, principal_urn: str, request: AccessRequest, principal: Principal = Depends(current_principal)
) -> dict:
    p_urn = await _authorize_project_governance(principal, name)
    _validate_project_relation(request.relation)
    await _require_grant_target(principal_urn, tenant_id=principal.tenant_id)
    await app.state.authz.write_relationship(
        resource_type="project", resource_urn=p_urn, relation=request.relation, subject_urn=principal_urn,
    )
    await _enqueue_permission_event(
        event_type="identity.permission.granted",
        target_principal_urn=principal_urn,
        resource_type="project",
        resource_urn=p_urn,
        relation=request.relation,
        actor=principal,
    )
    return {"status": "granted", "principalUrn": principal_urn, "project": name, "relation": request.relation}


@app.post("/projects/{name}/principals/{principal_urn:path}/access/revoke")
async def revoke_project_access(
    name: str, principal_urn: str, request: AccessRequest, principal: Principal = Depends(current_principal)
) -> dict:
    p_urn = await _authorize_project_governance(principal, name)
    _validate_project_relation(request.relation)
    await app.state.authz.delete_relationship(
        resource_type="project", resource_urn=p_urn, relation=request.relation, subject_urn=principal_urn,
    )

    await _enqueue_permission_event(
        event_type="identity.permission.revoked",
        target_principal_urn=principal_urn,
        resource_type="project",
        resource_urn=p_urn,
        relation=request.relation,
        actor=principal,
    )

    return {"status": "revoked", "principalUrn": principal_urn, "project": name, "relation": request.relation}


@app.get("/projects/{name}/access")
async def list_project_access(name: str, principal: Principal = Depends(current_principal)) -> list[dict]:
    """Who currently holds viewer/editor/admin on the project — direct
    grants only, exactly like the workspace listing; the workspace-tier
    cascade stays visible through `GET /access`, not duplicated here.
    `_authorize_project_governance` supplies both the 404 (unknown
    project) and the 403 (caller lacks project `approve`).
    """
    p_urn = await _authorize_project_governance(principal, name)
    return await _access_listing("project", p_urn, VALID_PROJECT_RELATIONS)
