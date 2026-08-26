"""Identity Platform — Tenant, Workspace, and Principal management.

Manages authentication, tenant/workspace/principal registration, and token issuance.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import Depends, FastAPI, Form, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from holon_common import (
    HolonError,
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
    decode_token,
    install_error_handlers,
    instrument_cors,
    instrument_metrics,
    instrument_tracing,
    issue_token,
    make_principal_dependency,
    mark_principal_disabled,
    outbox,
    retry_with_backoff,
    run_migrations,
    set_session_cookie,
)
from holon_common.audit import clear_durable_audit_hooks, emit_audit
from holon_common.audit_store import install_durable_audit
from holon_common.auth import COOKIE_NAME
from holon_common.readiness import check_kafka_producer, check_opa, check_postgres, check_spicedb, report_ready

from . import federation
from . import oidc as oidc_client
from . import saml as saml_client
from . import scim
from .seed import (
    VALID_PROJECT_RELATIONS,
    VALID_WORKSPACE_RELATIONS,
    create_project,
    create_tenant,
    create_workspace,
    ensure_instance_bootstrap,
    get_project,
    get_tenant,
    get_workspace,
    insert_principal,
    list_projects,
    list_tenants,
    list_workspaces,
    project_urn,
    set_tenant_status,
    set_workspace_status,
    tenant_urn,
    verify_and_migrate_secret,
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
    # Identity-owned tables live in app/migrations (0000_baseline + 0001–0002).
    # Shared holon_common helpers (audit/outbox) are included in 0000.
    await run_migrations(app.state.pool, Path(__file__).parent / "migrations")

    from .token_revocation import hydrate_local_denylist_from_db

    await hydrate_local_denylist_from_db(app.state.pool)

    clear_durable_audit_hooks()
    install_durable_audit(app.state.pool)

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
_base_principal = make_principal_dependency(JWT_SECRET, secrets=JWT_SECRETS, check_disabled_denylist=False)


async def current_principal(request: Request) -> Principal:
    principal = await _base_principal(request)
    from .token_revocation import is_jti_revoked_in_db

    if principal.jti and await is_jti_revoked_in_db(app.state.pool, principal.jti):
        raise HolonError.unauthorized("TokenRevoked", "token has been revoked")
    row = await app.state.pool.fetchrow("SELECT status, tenant_id FROM principal WHERE urn = $1", principal.urn)
    if row is None or row["status"] != "active":
        mark_principal_disabled(principal.urn)
        raise HolonError.unauthorized("PrincipalDisabled", "principal is disabled")
    tenant = await get_tenant(app.state.pool, row["tenant_id"])
    if tenant is None or tenant["status"] != "active":
        raise HolonError.forbidden("TenantDisabled", "tenant is disabled")
    return principal


app.include_router(scim.router, prefix="/scim/v2")

_AUTH_ATTEMPTS: OrderedDict[str, list[float]] = OrderedDict()
_AUTH_WINDOW_SECONDS = 60.0
_AUTH_MAX_ATTEMPTS = 10
_AUTH_ATTEMPTS_MAX_KEYS = 4096


def _rate_limit_auth(key: str) -> None:
    from holon_common.security_posture import is_production

    if not is_production():
        return
    now = time.monotonic()
    hits = [t for t in _AUTH_ATTEMPTS.pop(key, []) if now - t < _AUTH_WINDOW_SECONDS]
    if len(hits) >= _AUTH_MAX_ATTEMPTS:
        _AUTH_ATTEMPTS[key] = hits
        raise HolonError.rate_limited("RateLimited", "too many authentication attempts")
    hits.append(now)
    _AUTH_ATTEMPTS[key] = hits
    while len(_AUTH_ATTEMPTS) > _AUTH_ATTEMPTS_MAX_KEYS:
        _AUTH_ATTEMPTS.popitem(last=False)


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
        if k not in ("client_secret", "client_secret_hash", "status", "oidc_sub", "external_id")
    }
    return Principal(**fields)


async def _fetch_principal(pool: asyncpg.Pool, urn: str) -> Principal | None:
    row = await pool.fetchrow("SELECT * FROM principal WHERE urn = $1", urn)
    return _principal_from_row(row) if row else None


async def _require_active_principal_row(urn: str) -> asyncpg.Record:
    row = await app.state.pool.fetchrow("SELECT * FROM principal WHERE urn = $1", urn)
    if row is None or row["status"] != "active":
        raise HolonError.unauthorized('InvalidCredentials', "invalid principal_urn or client_secret")
    tenant = await get_tenant(app.state.pool, row["tenant_id"])
    if tenant is None or tenant["status"] != "active":
        raise HolonError.unauthorized('InvalidCredentials', "invalid principal_urn or client_secret")
    return row


def _reject_group_authentication(row: asyncpg.Record) -> None:
    if row["type"] == "group":
        raise HolonError.forbidden("GroupCannotAuthenticate", "groups cannot mint tokens or sign in")


def _grant_subject_relation(target: Principal) -> str | None:
    """Map principal to SpiceDB userset (group#member vs principal directly)."""
    return "member" if target.type == "group" else None


async def _require_grant_target(urn: str, *, tenant_id: str) -> Principal:
    target = await _fetch_principal(app.state.pool, urn)
    if target is None:
        raise HolonError.not_found('PrincipalNotFound', f"unknown principal: {urn}")
    if target.tenant_id != tenant_id:
        raise HolonError.invalid_argument('CrossTenantPrincipal', "principal belongs to another tenant")
    return target


async def _authorize_bootstrap_governance(principal: Principal) -> None:
    """Authorize tenant creation on the bootstrap workspace."""
    decision = await app.state.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=workspace_urn(TENANT_ID, WORKSPACE_ID),
        permission="approve",
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


async def _authorize_workspace_governance(principal: Principal, tenant_id: str, workspace_id: str) -> str:
    ws = await get_workspace(app.state.pool, workspace_id)
    if ws is None or ws["tenant_id"] != tenant_id:
        raise HolonError.not_found('WorkspaceNotFound', f"unknown workspace: {workspace_id}")
    if ws["status"] != "active":
        raise HolonError.invalid_argument('WorkspaceDisabled', "workspace is disabled")
    w_urn = workspace_urn(tenant_id, workspace_id)
    decision = await app.state.authz.authorize(
        principal, resource_type="workspace", resource_urn=w_urn, permission="approve"
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)
    return w_urn


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
        ]
    )


@app.get("/principals", response_model=list[Principal])
async def list_principals(principal: Principal = Depends(current_principal)) -> list[Principal]:
    """List principals belonging to the caller's tenant."""
    rows = await app.state.pool.fetch(
        "SELECT * FROM principal WHERE tenant_id = $1 ORDER BY urn", principal.tenant_id
    )
    return [_principal_from_row(row) for row in rows]


@app.post("/token")
async def mint_token(request: TokenRequest) -> dict:
    """Register a new principal and issue API client credentials."""
    _rate_limit_auth(request.principal_urn)
    row = await _require_active_principal_row(request.principal_urn)
    _reject_group_authentication(row)
    if not await verify_and_migrate_secret(app.state.pool, row, request.client_secret):
        raise HolonError.unauthorized('InvalidCredentials', "invalid principal_urn or client_secret")
    principal = _principal_from_row(row)
    return {"access_token": _issue(principal), "token_type": "bearer"}


@app.post("/api/oauth2/token")
async def oauth2_token(
    grant_type: str = Form("client_credentials"),
    client_id: str = Form(...),
    client_secret: str = Form(...),
    scope: str = Form(""),
) -> dict:
    """OAuth2 client_credentials token request (RFC 6749 §4.4.2)."""
    if grant_type != "client_credentials":
        raise HolonError.invalid_argument(
            "UnsupportedGrantType",
            f"unsupported grant_type: {grant_type}",
            grant_type=grant_type,
        )
    client_id = client_id.strip()
    _rate_limit_auth(client_id)
    if client_id.startswith("hl:"):
        principal_urn = client_id
    else:
        principal_urn = build_urn(TENANT_ID, "global", "user", client_id)
        # Also try service-account if user lookup fails below.
    try:
        row = await _require_active_principal_row(principal_urn)
    except HolonError:
        if client_id.startswith("hl:"):
            raise
        sa_urn = build_urn(TENANT_ID, "global", "service-account", client_id)
        row = await _require_active_principal_row(sa_urn)
    _reject_group_authentication(row)
    if not await verify_and_migrate_secret(app.state.pool, row, client_secret):
        raise HolonError.unauthorized("InvalidCredentials", "invalid client_id or client_secret")
    principal = _principal_from_row(row)
    _ = scope
    return {
        "access_token": _issue(principal),
        "token_type": "bearer",
        "expires_in": 3600,
    }

@app.post("/login")
async def login(request: TokenRequest, response: Response) -> dict:
    """Browser login endpoint issuing an HttpOnly session cookie."""
    try:
        _rate_limit_auth(request.principal_urn)
        row = await _require_active_principal_row(request.principal_urn)
        _reject_group_authentication(row)
        if not await verify_and_migrate_secret(app.state.pool, row, request.client_secret):
            raise HolonError.unauthorized('InvalidCredentials', "invalid principal_urn or client_secret")
    except HolonError as exc:
        emit_audit(
            category="identity",
            action="identity.login",
            outcome="failure",
            tenant_id=TENANT_ID,
            actor_urn=request.principal_urn,
            reason=exc.detail,
        )
        raise
    principal = _principal_from_row(row)
    set_session_cookie(response, _issue(principal))
    emit_audit(
        category="identity",
        action="identity.login",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
    )
    return {"status": "ok"}


@app.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    authorization = request.headers.get("authorization")
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
    else:
        token = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            session = decode_token(token, JWT_SECRET, secrets=JWT_SECRETS)
        except HolonError:
            session = None
        if session is not None and session.jti:
            import jwt as pyjwt
            from datetime import datetime, timezone

            from .token_revocation import enqueue_token_revoked

            claims = pyjwt.decode(token, options={"verify_signature": False})
            expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
            await enqueue_token_revoked(
                app.state.pool,
                jti=session.jti,
                principal_urn=session.urn,
                expires_at=expires_at,
                actor=session,
                tenant_id=session.tenant_id,
                workspace_id=WORKSPACE_ID,
            )
    clear_session_cookie(response)
    return {"status": "ok"}


_FEDERATED_ERROR_NAME = {"oidc": "OidcError", "saml": "SamlError"}


async def _delete_relationship_or_reraise(
    *,
    resource_type: str,
    resource_urn: str,
    relation: str,
    subject_urn: str,
) -> None:
    """Idempotently delete SpiceDB relationship, erroring on store unavailability."""
    import httpx

    try:
        await app.state.authz.delete_relationship(
            resource_type=resource_type,
            resource_urn=resource_urn,
            relation=relation,
            subject_urn=subject_urn,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return
        raise HolonError.unavailable("SpiceDbUnavailable", "authorization service error during grant sync") from exc
    except httpx.RequestError as exc:
        raise HolonError.unavailable("SpiceDbUnavailable", "authorization service unreachable during grant sync") from exc


async def _complete_federated_login(
    *,
    protocol: str,
    external_id: str,
    tenant_id: str,
    local_name: str,
    display_name: str,
    workspace_roles: dict[str, str],
    frontend_redirect: str,
) -> RedirectResponse:
    """Complete federated OIDC/SAML login and issue session cookie."""
    lookup_column = "oidc_sub" if protocol == "oidc" else "external_id"
    audit_action = f"identity.{protocol}.login"
    error_name = _FEDERATED_ERROR_NAME[protocol]

    tenant = await get_tenant(app.state.pool, tenant_id)
    if tenant is None or tenant["status"] != "active":
        emit_audit(
            category="identity",
            action=audit_action,
            outcome="failure",
            tenant_id=tenant_id,
            actor_urn=external_id,
            reason=f"unknown or disabled tenant: {tenant_id}",
        )
        raise HolonError.forbidden('TenantDisabled', f"unknown or disabled tenant for {protocol} login: {tenant_id}")

    row = await app.state.pool.fetchrow(f"SELECT * FROM principal WHERE {lookup_column} = $1", external_id)
    if row is None:
        try:
            created = await insert_principal(
                app.state.pool,
                tenant_id=tenant_id,
                type="user",
                local_name=local_name,
                display_name=display_name,
                **{lookup_column: external_id},
            )
        except asyncpg.UniqueViolationError as exc:
            raise HolonError.conflict(
                "FederatedLocalNameConflict",
                f"{protocol} identity maps to local_name {local_name!r} which already exists; "
                "refusing to attach this IdP subject to the existing principal",
            ) from exc
        await app.state.authz.write_relationship(
            resource_type="tenant",
            resource_urn=tenant_urn(tenant_id),
            relation="member",
            subject_urn=created["urn"],
        )
        row = await app.state.pool.fetchrow("SELECT * FROM principal WHERE urn = $1", created["urn"])
    else:
        if row["tenant_id"] != tenant_id:
            emit_audit(
                category="identity",
                action=audit_action,
                outcome="failure",
                tenant_id=row["tenant_id"],
                actor_urn=row["urn"],
                reason=f"{protocol} tenant claim mismatch",
            )
            raise HolonError.forbidden(error_name, (
                    f"{protocol} tenant claim {tenant_id!r} does not match linked principal "
                    f"tenant {row['tenant_id']!r}; unlink {lookup_column} or update the principal"
                ),)

    if row["status"] != "active":
        raise HolonError.forbidden('PrincipalDisabled', "principal is disabled")
    principal = _principal_from_row(row)

    # Group → workspace relation sync (admin/editor/viewer). Highest privilege wins;
    # alternate relations on the same workspace are removed for this principal.
    # Workspaces that disappeared from the IdP token are revoked (day-2 SSO).
    desired_ids = set(workspace_roles)
    for ws in await list_workspaces(app.state.pool, principal.tenant_id):
        if ws["workspace_id"] in desired_ids:
            continue
        w_urn = workspace_urn(principal.tenant_id, ws["workspace_id"])
        for relation in VALID_WORKSPACE_RELATIONS:
            await _delete_relationship_or_reraise(
                resource_type="workspace",
                resource_urn=w_urn,
                relation=relation,
                subject_urn=principal.urn,
            )
    synced: list[dict] = []
    for workspace_id, relation in workspace_roles.items():
        ws = await get_workspace(app.state.pool, workspace_id)
        if ws is None or ws["tenant_id"] != principal.tenant_id:
            continue
        w_urn = workspace_urn(principal.tenant_id, workspace_id)
        await app.state.authz.write_relationship(
            resource_type="workspace",
            resource_urn=w_urn,
            relation=relation,
            subject_urn=principal.urn,
        )
        for other in VALID_WORKSPACE_RELATIONS - {relation}:
            await _delete_relationship_or_reraise(
                resource_type="workspace",
                resource_urn=w_urn,
                relation=other,
                subject_urn=principal.urn,
            )
        synced.append({"workspaceId": workspace_id, "relation": relation})
        emit_audit(
            category="identity",
            action=f"identity.{protocol}.group_sync",
            outcome="success",
            tenant_id=principal.tenant_id,
            actor_urn=principal.urn,
            actor_type=principal.type,
            resource_type="workspace",
            resource_urn=w_urn,
            permission=relation,
            extra={"source": f"{protocol}_groups"},
        )

    emit_audit(
        category="identity",
        action=audit_action,
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        extra={"syncedWorkspaces": synced},
    )

    redirect = RedirectResponse(url=frontend_redirect, status_code=302)
    set_session_cookie(redirect, _issue(principal))
    return redirect


@app.get("/oidc/login")
async def oidc_login() -> dict:
    """Start OIDC authorization-code + PKCE. 404 when HOLON_OIDC_ISSUER unset."""
    if not oidc_client.oidc_enabled():
        raise HolonError.not_found('OidcNotConfigured', "OIDC is not configured")
    redirect_uri = os.environ.get(
        "HOLON_OIDC_REDIRECT_URI", "http://localhost:8001/oidc/callback"
    )
    return await oidc_client.build_authorize_url(app.state.pool, redirect_uri=redirect_uri)


@app.get("/oidc/callback")
async def oidc_callback(code: str, state: str):
    if not oidc_client.oidc_enabled():
        raise HolonError.not_found('OidcNotConfigured', "OIDC is not configured")
    try:
        claims = await oidc_client.exchange_code(app.state.pool, code=code, state=state)
    except Exception as exc:
        emit_audit(
            category="identity",
            action="identity.oidc.login",
            outcome="failure",
            tenant_id=TENANT_ID,
            reason=str(exc),
        )
        raise HolonError.unauthorized('OidcError', f"OIDC exchange failed: {exc}") from exc

    sub = str(claims.get("sub") or "")
    if not sub:
        emit_audit(
            category="identity",
            action="identity.oidc.login",
            outcome="failure",
            tenant_id=TENANT_ID,
            reason="OIDC claims missing sub",
        )
        raise HolonError.unauthorized('OidcError', "OIDC claims missing sub")

    tenant_id = federation.tenant_from_claims(claims, default_tenant=TENANT_ID)
    frontend = os.environ.get("HOLON_OIDC_POST_LOGIN_REDIRECT", "http://localhost:5173/objects")
    return await _complete_federated_login(
        protocol="oidc",
        external_id=sub,
        tenant_id=tenant_id,
        local_name=federation.local_name_from_claims(claims),
        display_name=federation.display_name_from_claims(claims),
        workspace_roles=federation.workspace_roles_from_claims(claims),
        frontend_redirect=frontend,
    )


@app.get("/saml/login")
async def saml_login(request: Request) -> RedirectResponse:
    """Start SAML SP-initiated SSO. 404 when no IdP metadata is configured."""
    if not saml_client.saml_enabled():
        raise HolonError.not_found('SamlNotConfigured', "SAML is not configured")
    url = saml_client.build_login_redirect(
        https=request.url.scheme == "https",
        http_host=request.url.hostname or "localhost",
        script_name=request.url.path,
    )
    return RedirectResponse(url=url, status_code=302)


@app.post("/saml/acs")
async def saml_acs(request: Request):
    """SAML Assertion Consumer Service — validates the IdP's signed
    response, then completes login via the same path OIDC uses."""
    if not saml_client.saml_enabled():
        raise HolonError.not_found('SamlNotConfigured', "SAML is not configured")
    form = await request.form()
    post_params = {key: value for key, value in form.items()}
    try:
        claims = saml_client.process_acs_response(
            https=request.url.scheme == "https",
            http_host=request.url.hostname or "localhost",
            script_name=request.url.path,
            post_params=post_params,
        )
    except Exception as exc:
        emit_audit(
            category="identity",
            action="identity.saml.login",
            outcome="failure",
            tenant_id=TENANT_ID,
            reason=str(exc),
        )
        raise HolonError.unauthorized('SamlError', f"SAML assertion invalid: {exc}") from exc

    assertion_id = claims.pop("_assertion_id", None)
    if assertion_id:
        await app.state.pool.execute(
            "DELETE FROM saml_seen_assertion WHERE seen_at < now() - interval '1 day'"
        )
        inserted = await app.state.pool.fetchval(
            "INSERT INTO saml_seen_assertion (assertion_id) VALUES ($1) "
            "ON CONFLICT DO NOTHING RETURNING assertion_id",
            assertion_id,
        )
        if inserted is None:
            raise HolonError.unauthorized("SamlError", "SAML assertion replayed")
    tenant_id = federation.tenant_from_claims(claims, default_tenant=TENANT_ID)
    frontend = os.environ.get(
        "HOLON_SAML_POST_LOGIN_REDIRECT",
        os.environ.get("HOLON_OIDC_POST_LOGIN_REDIRECT", "http://localhost:5173/objects"),
    )
    return await _complete_federated_login(
        protocol="saml",
        external_id=claims["sub"],
        tenant_id=tenant_id,
        local_name=federation.local_name_from_claims(claims),
        display_name=federation.display_name_from_claims(claims),
        workspace_roles=federation.workspace_roles_from_claims(claims),
        frontend_redirect=frontend,
    )


@app.get("/saml/metadata")
async def saml_metadata() -> Response:
    """SP metadata XML for the IdP-side setup — not gated on
    `saml_enabled()` since an operator configuring the IdP integration
    needs this before HOLON_SAML_IDP_METADATA_* can be set."""
    xml = saml_client.build_sp_metadata_xml()
    return Response(content=xml, media_type="application/xml")


@app.get("/whoami", response_model=Principal)
async def whoami(principal: Principal = Depends(current_principal)) -> Principal:
    return principal


@app.get("/internal/revocation-snapshot")
async def revocation_snapshot(request: Request) -> dict:
    """Durable denylist for other services to hydrate after a restart.

    Service-account / agent JWT only — a user token must not list every
    disabled principal. Identity itself loads this from Postgres on boot.
    """
    principal = await _base_principal(request)
    if principal.type not in {"service_account", "agent"}:
        raise HolonError.forbidden("SnapshotForbidden", "revocation snapshot is internal")
    from .token_revocation import load_revocation_snapshot

    return await load_revocation_snapshot(app.state.pool)


@app.get("/.well-known/jwks.json")
async def jwks() -> dict:
    """Public JWKS for RS256 verify pods — private keys stay on Identity only.

    HS256 deployments return an empty key set (shared secret is not published).
    """
    from holon_common.auth import jwt_algorithm, load_jwt_verify_keys

    if jwt_algorithm() != "RS256":
        return {"keys": []}
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend
        import base64
    except ImportError as exc:
        raise HolonError.unavailable("JwksUnavailable", "cryptography required for JWKS") from exc

    keys = []
    for kid, pem in load_jwt_verify_keys().items():
        public = serialization.load_pem_public_key(pem.encode(), backend=default_backend())
        numbers = public.public_numbers()

        def _b64url_int(value: int) -> str:
            raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        keys.append(
            {
                "kty": "RSA",
                "kid": kid,
                "use": "sig",
                "alg": "RS256",
                "n": _b64url_int(numbers.n),
                "e": _b64url_int(numbers.e),
            }
        )
    return {"keys": keys}


@app.get("/audit-events")
async def list_identity_audit_events(
    principal: Principal = Depends(current_principal),
    category: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    outcome: str | None = None,
    pageSize: int | None = None,
    pageToken: str | None = None,
    workspace_id: str | None = None,
) -> dict:
    """Queryable Identity durable audit trail (login, grants, OIDC sync).

    Distinct from Knowledge ``/api/holon/audit-events`` — each service owns
    its Postgres ``audit_event`` table. Requires workspace ``approve``.
    """
    import base64
    import json as json_mod

    from holon_common.audit import CATEGORIES
    from holon_common.audit_store import list_events

    tid, _wid = await _resolve_workspace_governance(
        principal, tenant_id=principal.tenant_id, workspace_id=workspace_id
    )
    if category is not None and category not in CATEGORIES:
        raise HolonError.invalid_argument("InvalidAuditCategory", f"unknown category: {category}", category=category)
    page_size = 50 if pageSize is None else pageSize
    if page_size < 1 or page_size > 100:
        raise HolonError.invalid_argument("InvalidPageSize", "pageSize must be between 1 and 100")
    after_id = None
    if pageToken:
        try:
            padded = pageToken + "=" * (-len(pageToken) % 4)
            payload = json_mod.loads(base64.urlsafe_b64decode(padded.encode()))
            after_id = int(payload["after_id"])
        except Exception as exc:
            raise HolonError.invalid_argument("InvalidPageToken", "invalid pageToken") from exc

    rows = await list_events(
        app.state.pool,
        tid,
        category=category,
        action=action,
        actor_urn=actor,
        outcome=outcome,
        after_id=after_id,
        page_size=page_size + 1,
    )
    next_token = None
    if len(rows) > page_size:
        rows = rows[:page_size]
        raw = json_mod.dumps({"after_id": rows[-1]["id"]}, separators=(",", ":")).encode()
        next_token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return {"data": rows, "nextPageToken": next_token, "pageSize": page_size}




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
    type: str = Field(pattern=r"^(user|agent|service_account|group)$")
    local_name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    display_name: str = Field(min_length=1, max_length=256)
    country: str | None = None
    on_behalf_of: str | None = None
    client_secret: str | None = Field(default=None, min_length=8, max_length=256)


class StatusRequest(BaseModel):
    status: str = Field(pattern=r"^(active|disabled)$")


@app.get("/tenants")
async def tenants_list(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_bootstrap_governance(principal)
    return await list_tenants(app.state.pool)


@app.post("/tenants", status_code=201)
async def tenants_create(request: CreateTenantRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Create a new tenant."""
    await _authorize_bootstrap_governance(principal)
    if await get_tenant(app.state.pool, request.tenant_id) is not None:
        raise HolonError.conflict('TenantAlreadyExists', f"tenant already exists: {request.tenant_id}")
    return await create_tenant(app.state.pool, tenant_id=request.tenant_id, display_name=request.display_name)


@app.post("/tenants/{tenant_id}/status")
async def tenants_set_status(
    tenant_id: str, request: StatusRequest, principal: Principal = Depends(current_principal)
) -> dict:
    await _authorize_bootstrap_governance(principal)
    if await get_tenant(app.state.pool, tenant_id) is None:
        raise HolonError.not_found('TenantNotFound', f"unknown tenant: {tenant_id}")
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
    except HolonError:
        return await list_workspaces(app.state.pool, principal.tenant_id)


@app.post("/workspaces", status_code=201)
async def workspaces_create(
    request: CreateWorkspaceRequest, principal: Principal = Depends(current_principal)
) -> dict:
    tenant = await get_tenant(app.state.pool, request.tenant_id)
    if tenant is None:
        raise HolonError.not_found('TenantNotFound', f"unknown tenant: {request.tenant_id}")
    if tenant["status"] != "active":
        raise HolonError.invalid_argument('TenantDisabled', "tenant is disabled")
    # Bootstrap admins may create the first workspace on a new filiale;
    # otherwise require approve on an existing workspace in that tenant.
    existing = await list_workspaces(app.state.pool, request.tenant_id)
    if not existing:
        await _authorize_bootstrap_governance(principal)
    else:
        await _authorize_workspace_governance(principal, request.tenant_id, existing[0]["workspace_id"])
    if await get_workspace(app.state.pool, request.workspace_id) is not None:
        raise HolonError.conflict('WorkspaceAlreadyExists', f"workspace already exists: {request.workspace_id}")

    # Never grant workspace admin to a principal from another tenant —
    # instance admins nominate a same-tenant `initial_admin_urn`.
    if principal.tenant_id == request.tenant_id:
        admin_urn = principal.urn
    else:
        if not request.initial_admin_urn:
            raise HolonError.invalid_argument('InitialAdminRequired', "initial_admin_urn is required when creating a workspace outside your tenant "
                "(create the filiale principal first, then pass their URN)",)
        admin = await _fetch_principal(app.state.pool, request.initial_admin_urn)
        if admin is None:
            raise HolonError.not_found('PrincipalNotFound', f"unknown principal: {request.initial_admin_urn}")
        if admin.tenant_id != request.tenant_id:
            raise HolonError.invalid_argument('InitialAdminTenantMismatch', "initial_admin_urn must belong to the workspace's tenant")
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
        raise HolonError.not_found('WorkspaceNotFound', f"unknown workspace: {workspace_id}")
    await _authorize_workspace_governance(principal, ws["tenant_id"], workspace_id)
    updated = await set_workspace_status(app.state.pool, workspace_id, request.status)
    return updated  # type: ignore[return-value]


@app.post("/principals", status_code=201)
async def principals_create(
    request: CreatePrincipalRequest, principal: Principal = Depends(current_principal)
) -> dict:
    tenant = await get_tenant(app.state.pool, request.tenant_id)
    if tenant is None:
        raise HolonError.not_found('TenantNotFound', f"unknown tenant: {request.tenant_id}")
    workspaces = await list_workspaces(app.state.pool, request.tenant_id)
    if not workspaces:
        await _authorize_bootstrap_governance(principal)
    else:
        await _authorize_workspace_governance(principal, request.tenant_id, workspaces[0]["workspace_id"])
    if request.type == "group" and request.on_behalf_of:
        raise HolonError.invalid_argument("GroupCannotDelegate", "a group cannot act on behalf of another principal")
    try:
        row = await insert_principal(
            app.state.pool,
            tenant_id=request.tenant_id,
            type=request.type,
            local_name=request.local_name,
            display_name=request.display_name,
            country=request.country,
            on_behalf_of=request.on_behalf_of,
            client_secret=request.client_secret,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HolonError.conflict('PrincipalAlreadyExists', "principal already exists") from exc
    await app.state.authz.write_relationship(
        resource_type="tenant",
        resource_urn=tenant_urn(request.tenant_id),
        relation="member",
        subject_urn=row["urn"],
    )
    # Never return client_secret in list form; include once at create for
    # service accounts / users. Groups cannot authenticate.
    payload = {
        "urn": row["urn"],
        "type": row["type"],
        "tenant_id": row["tenant_id"],
        "display_name": row["display_name"],
        "on_behalf_of": row["on_behalf_of"],
        "country": row["country"],
        "status": row["status"],
    }
    if request.type != "group":
        payload["client_secret"] = row["client_secret"]
    return payload


@app.post("/principals/{principal_urn:path}/status")
async def principals_set_status(
    principal_urn: str, request: StatusRequest, principal: Principal = Depends(current_principal)
) -> dict:
    target = await _fetch_principal(app.state.pool, principal_urn)
    if target is None:
        raise HolonError.not_found('PrincipalNotFound', f"unknown principal: {principal_urn}")
    workspaces = await list_workspaces(app.state.pool, target.tenant_id)
    if not workspaces:
        await _authorize_bootstrap_governance(principal)
    else:
        await _authorize_workspace_governance(principal, target.tenant_id, workspaces[0]["workspace_id"])
    updated = await _enqueue_principal_status_event(
        target_principal_urn=principal_urn,
        status=request.status,
        actor=principal,
        tenant_id=target.tenant_id,
    )
    assert updated is not None
    return {k: updated[k] for k in ("urn", "type", "tenant_id", "display_name", "status")}


class GroupMemberRequest(BaseModel):
    principal_urn: str


async def _require_group(group_urn: str) -> Principal:
    group = await _fetch_principal(app.state.pool, group_urn)
    if group is None:
        raise HolonError.not_found("PrincipalNotFound", f"unknown principal: {group_urn}")
    if group.type != "group":
        raise HolonError.invalid_argument("NotAGroup", f"{group_urn} is not a group", urn=group_urn)
    return group


async def _authorize_principal_governance(principal: Principal, tenant_id: str) -> None:
    workspaces = await list_workspaces(app.state.pool, tenant_id)
    if not workspaces:
        await _authorize_bootstrap_governance(principal)
    else:
        await _authorize_workspace_governance(principal, tenant_id, workspaces[0]["workspace_id"])


@app.get("/principals/{group_urn:path}/members")
async def list_group_members(group_urn: str, principal: Principal = Depends(current_principal)) -> list[dict]:
    group = await _require_group(group_urn)
    if group.tenant_id != principal.tenant_id:
        raise HolonError.invalid_argument("CrossTenantPrincipal", "principal belongs to another tenant")
    return await _access_listing("principal", group_urn, {"member"})


@app.post("/principals/{group_urn:path}/members", status_code=201)
async def add_group_member(
    group_urn: str, request: GroupMemberRequest, principal: Principal = Depends(current_principal)
) -> dict:
    group = await _require_group(group_urn)
    await _authorize_principal_governance(principal, group.tenant_id)
    member = await _require_grant_target(request.principal_urn, tenant_id=group.tenant_id)
    if member.type == "group":
        raise HolonError.invalid_argument("NestedGroupForbidden", "group membership is one level only")
    if member.urn == group.urn:
        raise HolonError.invalid_argument("GroupCannotContainSelf", "a group cannot contain itself")
    await app.state.authz.write_relationship(
        resource_type="principal",
        resource_urn=group.urn,
        relation="member",
        subject_urn=member.urn,
    )
    await _enqueue_permission_event(
        event_type="identity.permission.granted",
        target_principal_urn=member.urn,
        resource_type="principal",
        resource_urn=group.urn,
        relation="member",
        actor=principal,
        tenant_id=group.tenant_id,
    )
    emit_audit(
        category="identity",
        action="identity.group.member_added",
        outcome="success",
        tenant_id=group.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="principal",
        resource_urn=group.urn,
        extra={"memberUrn": member.urn},
    )
    return {"status": "added", "groupUrn": group.urn, "memberUrn": member.urn}


@app.delete("/principals/{group_urn:path}/members/{member_urn:path}")
async def remove_group_member(
    group_urn: str, member_urn: str, principal: Principal = Depends(current_principal)
) -> dict:
    group = await _require_group(group_urn)
    await _authorize_principal_governance(principal, group.tenant_id)
    member = await _require_grant_target(member_urn, tenant_id=group.tenant_id)
    await app.state.authz.delete_relationship(
        resource_type="principal",
        resource_urn=group.urn,
        relation="member",
        subject_urn=member.urn,
    )
    await _enqueue_permission_event(
        event_type="identity.permission.revoked",
        target_principal_urn=member.urn,
        resource_type="principal",
        resource_urn=group.urn,
        relation="member",
        actor=principal,
        tenant_id=group.tenant_id,
    )
    emit_audit(
        category="identity",
        action="identity.group.member_removed",
        outcome="success",
        tenant_id=group.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="principal",
        resource_urn=group.urn,
        extra={"memberUrn": member.urn},
    )
    return {"status": "removed", "groupUrn": group.urn, "memberUrn": member.urn}


async def _resolve_workspace_governance(
    principal: Principal, *, tenant_id: str, workspace_id: str | None
) -> tuple[str, str]:
    """Authorize workspace governance permissions for a tenant."""
    if workspace_id:
        await _authorize_workspace_governance(principal, tenant_id, workspace_id)
        return tenant_id, workspace_id
    if tenant_id == TENANT_ID:
        await _authorize_workspace_governance(principal, TENANT_ID, WORKSPACE_ID)
        return TENANT_ID, WORKSPACE_ID
    workspaces = await list_workspaces(app.state.pool, tenant_id)
    if not workspaces:
        raise HolonError.invalid_argument('TenantHasNoWorkspace', f"tenant {tenant_id!r} has no workspace", tenant_id=tenant_id)
    last_exc: HolonError | None = None
    for ws in workspaces:
        try:
            await _authorize_workspace_governance(principal, tenant_id, ws["workspace_id"])
            return tenant_id, ws["workspace_id"]
        except HolonError as exc:
            if exc.status_code == 403:
                last_exc = exc
                continue
            raise
    raise last_exc or HolonError.forbidden(
        "PermissionDenied", "access denied: workspace approve required"
    )


async def _access_listing(resource_type: str, resource_urn: str, valid_relations: set[str]) -> list[dict]:
    """Enumerate direct ReBAC grants on a resource."""
    relationships = await app.state.authz.read_relationships(resource_type=resource_type, resource_urn=resource_urn)
    rows = await app.state.pool.fetch("SELECT * FROM principal")
    from holon_common.spicedb_id import index_by_spicedb_object_id

    by_object_id = index_by_spicedb_object_id(rows)

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
        raise HolonError.invalid_argument('InvalidWorkspaceRelation', f"invalid relation: {relation!r} (must be one of {sorted(VALID_WORKSPACE_RELATIONS)})",
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


async def _enqueue_principal_status_event(
    *,
    target_principal_urn: str,
    status: str,
    actor: Principal,
    tenant_id: str,
) -> dict | None:
    from .status_events import enqueue_principal_status_event

    return await enqueue_principal_status_event(
        app.state.pool,
        target_principal_urn=target_principal_urn,
        status=status,
        actor=actor,
        tenant_id=tenant_id,
        workspace_id=WORKSPACE_ID,
    )


async def _fanout_group_permission_event(
    group: Principal,
    *,
    event_type: str,
    resource_type: str,
    resource_urn: str,
    relation: str,
    actor: Principal,
    tenant_id: str,
    workspace_id: str | None = None,
) -> None:
    """Invalidate ReBAC permission caches for group members."""
    members = await _access_listing("principal", group.urn, {"member"})
    for member in members:
        await _enqueue_permission_event(
            event_type=event_type,
            target_principal_urn=member["principal_urn"],
            resource_type=resource_type,
            resource_urn=resource_urn,
            relation=relation,
            actor=actor,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )


@app.post("/principals/{principal_urn:path}/access/grant")
async def grant_access(
    principal_urn: str, request: AccessRequest, principal: Principal = Depends(current_principal)
) -> dict:
    _validate_relation(request.relation)
    target = await _fetch_principal(app.state.pool, principal_urn)
    if target is None:
        raise HolonError.not_found('PrincipalNotFound', f"unknown principal: {principal_urn}")
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
        optional_subject_relation=_grant_subject_relation(target),
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
    if target.type == "group":
        await _fanout_group_permission_event(
            target,
            event_type="identity.permission.granted",
            resource_type="workspace",
            resource_urn=w_urn,
            relation=request.relation,
            actor=principal,
            tenant_id=tid,
            workspace_id=wid,
        )
    emit_audit(
        category="identity",
        action="identity.permission.granted",
        outcome="success",
        tenant_id=tid,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="workspace",
        resource_urn=w_urn,
        permission=request.relation,
        reason=f"granted {request.relation} to {principal_urn}",
        extra={"targetPrincipalUrn": principal_urn},
    )
    return {"status": "granted", "principalUrn": principal_urn, "relation": request.relation, "workspace_id": wid}


@app.post("/principals/{principal_urn:path}/access/revoke")
async def revoke_access(
    principal_urn: str, request: AccessRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Revoke workspace access for a principal."""
    _validate_relation(request.relation)
    target = await _fetch_principal(app.state.pool, principal_urn)
    if target is None:
        raise HolonError.not_found('PrincipalNotFound', f"unknown principal: {principal_urn}")
    tid, wid = await _resolve_workspace_governance(
        principal, tenant_id=target.tenant_id, workspace_id=request.workspace_id
    )

    w_urn = workspace_urn(tid, wid)
    await app.state.authz.delete_relationship(
        resource_type="workspace",
        resource_urn=w_urn,
        relation=request.relation,
        subject_urn=principal_urn,
        optional_subject_relation=_grant_subject_relation(target),
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
    if target.type == "group":
        await _fanout_group_permission_event(
            target,
            event_type="identity.permission.revoked",
            resource_type="workspace",
            resource_urn=w_urn,
            relation=request.relation,
            actor=principal,
            tenant_id=tid,
            workspace_id=wid,
        )

    emit_audit(
        category="identity",
        action="identity.permission.revoked",
        outcome="success",
        tenant_id=tid,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="workspace",
        resource_urn=w_urn,
        permission=request.relation,
        reason=f"revoked {request.relation} from {principal_urn}",
        extra={"targetPrincipalUrn": principal_urn},
    )

    return {"status": "revoked", "principalUrn": principal_urn, "relation": request.relation, "workspace_id": wid}


@app.get("/access")
async def list_workspace_access(
    workspace_id: str | None = None, principal: Principal = Depends(current_principal)
) -> list[dict]:
    """List principals holding access relations on the workspace."""
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
    """Create a project within a workspace."""
    tid, wid = await _resolve_workspace_governance(
        principal, tenant_id=principal.tenant_id, workspace_id=None
    )
    urn = project_urn(tid, wid, request.name)
    if await get_project(app.state.pool, urn) is not None:
        raise HolonError.conflict('ProjectAlreadyExists', f"project already exists: {request.name}", name=request.name)
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
        raise HolonError.not_found('ProjectNotFound', f"unknown project: {name}")
    return project


async def _authorize_project_governance(principal: Principal, project_name: str) -> str:
    """Authorize project governance permissions."""
    projects = await list_projects(app.state.pool, principal.tenant_id)
    project = next((p for p in projects if p["name"] == project_name), None)
    if project is None:
        raise HolonError.not_found('ProjectNotFound', f"unknown project: {project_name}")
    urn = project["urn"]
    decision = await app.state.authz.authorize(principal, resource_type="project", resource_urn=urn, permission="approve")
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)
    return urn


def _validate_project_relation(relation: str) -> None:
    if relation not in VALID_PROJECT_RELATIONS:
        raise HolonError.invalid_argument('InvalidProjectRelation', f"invalid relation: {relation!r} (must be one of {sorted(VALID_PROJECT_RELATIONS)})",
        )


@app.post("/projects/{name}/principals/{principal_urn:path}/access/grant")
async def grant_project_access(
    name: str, principal_urn: str, request: AccessRequest, principal: Principal = Depends(current_principal)
) -> dict:
    p_urn = await _authorize_project_governance(principal, name)
    _validate_project_relation(request.relation)
    target = await _require_grant_target(principal_urn, tenant_id=principal.tenant_id)
    await app.state.authz.write_relationship(
        resource_type="project",
        resource_urn=p_urn,
        relation=request.relation,
        subject_urn=principal_urn,
        optional_subject_relation=_grant_subject_relation(target),
    )
    await _enqueue_permission_event(
        event_type="identity.permission.granted",
        target_principal_urn=principal_urn,
        resource_type="project",
        resource_urn=p_urn,
        relation=request.relation,
        actor=principal,
    )
    if target.type == "group":
        await _fanout_group_permission_event(
            target,
            event_type="identity.permission.granted",
            resource_type="project",
            resource_urn=p_urn,
            relation=request.relation,
            actor=principal,
            tenant_id=principal.tenant_id,
        )
    return {"status": "granted", "principalUrn": principal_urn, "project": name, "relation": request.relation}


@app.post("/projects/{name}/principals/{principal_urn:path}/access/revoke")
async def revoke_project_access(
    name: str, principal_urn: str, request: AccessRequest, principal: Principal = Depends(current_principal)
) -> dict:
    p_urn = await _authorize_project_governance(principal, name)
    _validate_project_relation(request.relation)
    target = await _require_grant_target(principal_urn, tenant_id=principal.tenant_id)
    await app.state.authz.delete_relationship(
        resource_type="project",
        resource_urn=p_urn,
        relation=request.relation,
        subject_urn=principal_urn,
        optional_subject_relation=_grant_subject_relation(target),
    )

    await _enqueue_permission_event(
        event_type="identity.permission.revoked",
        target_principal_urn=principal_urn,
        resource_type="project",
        resource_urn=p_urn,
        relation=request.relation,
        actor=principal,
    )
    if target.type == "group":
        await _fanout_group_permission_event(
            target,
            event_type="identity.permission.revoked",
            resource_type="project",
            resource_urn=p_urn,
            relation=request.relation,
            actor=principal,
            tenant_id=principal.tenant_id,
        )

    return {"status": "revoked", "principalUrn": principal_urn, "project": name, "relation": request.relation}


@app.get("/projects/{name}/access")
async def list_project_access(name: str, principal: Principal = Depends(current_principal)) -> list[dict]:
    """List principals with direct grants on the project."""
    p_urn = await _authorize_project_governance(principal, name)
    return await _access_listing("project", p_urn, VALID_PROJECT_RELATIONS)
