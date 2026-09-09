"""Token minting, session login/logout, JWKS, whoami, audit."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, Response

from holon_common import HolonError, Principal, build_urn, decode_token, clear_session_cookie, set_session_cookie
from holon_common.auth import COOKIE_NAME
from holon_common.audit import emit_audit

from .. import deps
from ..deps import (
    JWT_SECRET,
    JWT_SECRETS,
    TENANT_ID,
    TokenRequest,
    WORKSPACE_ID,
    _base_principal,
    _issue,
    _principal_from_row,
    _rate_limit_auth,
    _reject_group_authentication,
    _require_active_principal_row,
    _resolve_workspace_governance,
    current_principal,
)
from ..seed import verify_and_migrate_secret


router = APIRouter()


@router.post("/token")
async def mint_token(request: TokenRequest) -> dict:
    """Register a new principal and issue API client credentials."""
    _rate_limit_auth(request.principal_urn)
    row = await _require_active_principal_row(request.principal_urn)
    _reject_group_authentication(row)
    if not await verify_and_migrate_secret(deps.pool, row, request.client_secret):
        raise HolonError.unauthorized('InvalidCredentials', "invalid principal_urn or client_secret")
    principal = _principal_from_row(row)
    return {"access_token": _issue(principal), "token_type": "bearer"}



@router.post("/api/oauth2/token")
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
    if not await verify_and_migrate_secret(deps.pool, row, client_secret):
        raise HolonError.unauthorized("InvalidCredentials", "invalid client_id or client_secret")
    principal = _principal_from_row(row)
    _ = scope
    return {
        "access_token": _issue(principal),
        "token_type": "bearer",
        "expires_in": 3600,
    }



@router.post("/login")
async def login(request: TokenRequest, response: Response) -> dict:
    """Browser login endpoint issuing an HttpOnly session cookie."""
    try:
        _rate_limit_auth(request.principal_urn)
        row = await _require_active_principal_row(request.principal_urn)
        _reject_group_authentication(row)
        if not await verify_and_migrate_secret(deps.pool, row, request.client_secret):
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



@router.post("/logout")
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

            from ..token_revocation import enqueue_token_revoked

            claims = pyjwt.decode(token, options={"verify_signature": False})
            expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
            await enqueue_token_revoked(
                deps.pool,
                jti=session.jti,
                principal_urn=session.urn,
                expires_at=expires_at,
                actor=session,
                tenant_id=session.tenant_id,
                workspace_id=WORKSPACE_ID,
            )
    clear_session_cookie(response)
    return {"status": "ok"}



@router.get("/whoami", response_model=Principal)
async def whoami(principal: Principal = Depends(current_principal)) -> Principal:
    return principal



@router.get("/internal/revocation-snapshot")
async def revocation_snapshot(request: Request) -> dict:
    """Durable denylist for other services to hydrate after a restart.

    Service-account / agent JWT only — a user token must not list every
    disabled principal. Identity itself loads this from Postgres on boot.
    """
    principal = await _base_principal(request)
    if principal.type not in {"service_account", "agent"}:
        raise HolonError.forbidden("SnapshotForbidden", "revocation snapshot is internal")
    from ..token_revocation import load_revocation_snapshot

    return await load_revocation_snapshot(deps.pool)



@router.get("/.well-known/jwks.json")
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



@router.get("/audit-events")
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
        deps.pool,
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

