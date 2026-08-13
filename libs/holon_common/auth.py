"""Identity primitives.

`Principal` and its JWT encoding form the identity primitives.
`holon_common.authz.PermissionClient` handles authorization decisions.

**Key rotation.** `HOLON_JWT_SECRETS` is a `kid:secret` map
(`kid1:secretA,kid2:secretB` or JSON object). `HOLON_JWT_SECRET` (singular)
remains supported as the single active key with kid `default`.
Services boot via `active_jwt()` and pass `kid`+`secrets` into `issue_token`
so the active kid is stamped; `decode_token` looks up the header kid then
verifies — 401 if kid missing/unknown.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

import jwt
from fastapi import HTTPException, Request, Response, status
from pydantic import BaseModel

COOKIE_NAME = "holon_session"
SESSION_COOKIE_TTL_SECONDS = 3600


class Principal(BaseModel):
    urn: str
    type: str  # user | service_account | agent
    tenant_id: str
    display_name: str
    on_behalf_of: Optional[str] = None
    country: Optional[str] = None


def load_jwt_secrets() -> tuple[dict[str, str], str]:
    """Returns (kid→secret map, active_kid)."""
    multi = (os.environ.get("HOLON_JWT_SECRETS") or "").strip() or None
    if multi:
        if multi.startswith("{"):
            mapping = {str(k): str(v) for k, v in json.loads(multi).items()}
        else:
            mapping = {}
            for part in multi.split(","):
                kid, _, secret = part.partition(":")
                if not kid or not secret:
                    raise ValueError(f"invalid HOLON_JWT_SECRETS entry: {part!r}")
                mapping[kid.strip()] = secret.strip()
        active = os.environ.get("HOLON_JWT_ACTIVE_KID") or next(iter(mapping))
        if active not in mapping:
            raise ValueError(f"HOLON_JWT_ACTIVE_KID {active!r} not in HOLON_JWT_SECRETS")
        return mapping, active
    singular = os.environ.get("HOLON_JWT_SECRET")
    if not singular:
        raise RuntimeError("HOLON_JWT_SECRET or HOLON_JWT_SECRETS required")
    return {"default": singular}, "default"


def active_jwt() -> tuple[str, str, dict[str, str]]:
    """Returns `(active_secret, active_kid, secrets_map)` for service boot."""
    secrets, active = load_jwt_secrets()
    return secrets[active], active, secrets


def _principal_mint_allowed(urn: str, allowed: set[str]) -> bool:
    """True if `urn` is allowlisted by full URN or local-name suffix after last `:`."""
    if urn in allowed:
        return True
    local = urn.rsplit(":", 1)[-1] if urn else ""
    return bool(local) and local in allowed


def issue_token(
    principal: Principal,
    secret: str,
    ttl_seconds: int = SESSION_COOKIE_TTL_SECONDS,
    *,
    kid: Optional[str] = None,
    secrets: Optional[dict[str, str]] = None,
    allow_user: bool = False,
) -> str:
    """`secret` is the signing key. When `secrets`+`kid` are provided
    (rotation), the kid is stamped into the JWT header.

    Non-Identity services must mint only service_account/agent tokens
    (`allow_user=False`, the default). User session tokens are Identity's
    (and Experience self-refresh) job — holding HOLON_JWT_SECRET alone
    must not let a compromised pod impersonate arbitrary humans.

    Production also requires `HOLON_ALLOW_USER_JWT_MINT` for user mints and
    `HOLON_MINTABLE_PRINCIPAL_URNS` for SA/agent mints (see SECURITY.md).
    """
    from .security_posture import is_production

    if principal.type == "user" and not allow_user:
        # Escape hatch for local unit tests that mint users without Identity.
        if os.environ.get("HOLON_ALLOW_LOCAL_USER_MINT", "").lower() in {"1", "true", "yes"}:
            pass
        else:
            raise ValueError(
                "refusing to mint a user JWT outside Identity — pass allow_user=True "
                "only from Identity/Experience session paths"
            )
    if principal.type == "user" and allow_user and is_production():
        if os.environ.get("HOLON_ALLOW_USER_JWT_MINT", "").strip().lower() not in {"1", "true", "yes"}:
            raise ValueError(
                "refusing to mint a user JWT in production — set HOLON_ALLOW_USER_JWT_MINT=true "
                "only on Identity"
            )
    if principal.type != "user":
        # Identity is the issuer of record after client_secret checks on /token —
        # it may mint any authenticated SA/agent. Non-Identity services are
        # constrained to HOLON_MINTABLE_PRINCIPAL_URNS so a compromised pod
        # cannot mint arbitrary service identities.
        identity_issuer = os.environ.get("HOLON_ALLOW_USER_JWT_MINT", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        if not identity_issuer:
            mintable_raw = os.environ.get("HOLON_MINTABLE_PRINCIPAL_URNS")
            if mintable_raw is not None:
                allowed = {p.strip() for p in mintable_raw.split(",") if p.strip()}
                if not _principal_mint_allowed(principal.urn, allowed):
                    raise ValueError(
                        f"refusing to mint JWT for {principal.urn!r} — not in HOLON_MINTABLE_PRINCIPAL_URNS"
                    )
            elif is_production():
                raise ValueError(
                    "refusing to mint service_account/agent JWT in production — "
                    "set HOLON_MINTABLE_PRINCIPAL_URNS (comma-separated URNs or local names; "
                    "empty string if this service never mints SAs)"
                )
    now = int(time.time())
    payload = {
        "sub": principal.urn,
        "type": principal.type,
        "tenant_id": principal.tenant_id,
        "display_name": principal.display_name,
        "on_behalf_of": principal.on_behalf_of,
        "country": principal.country,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    headers = {"kid": kid} if kid else None
    sign_key = secrets[kid] if secrets and kid else secret
    return jwt.encode(payload, sign_key, algorithm="HS256", headers=headers)


def decode_token(token: str, secret: str, *, secrets: Optional[dict[str, str]] = None) -> Principal:
    try:
        if secrets:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid") or "default"
            if kid not in secrets:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"unknown jwt kid: {kid}")
            verify_key = secrets[kid]
        else:
            verify_key = secret
        payload = jwt.decode(token, verify_key, algorithms=["HS256"])
    except HTTPException:
        raise
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid token: {exc}") from exc
    return Principal(
        urn=payload["sub"],
        type=payload["type"],
        tenant_id=payload["tenant_id"],
        display_name=payload["display_name"],
        on_behalf_of=payload.get("on_behalf_of"),
        country=payload.get("country"),
    )


def make_principal_dependency(secret: str, *, expected_tenant_id: Optional[str] = None, secrets: Optional[dict[str, str]] = None):
    """Builds a FastAPI dependency that resolves the current Principal from
    either the Authorization header or the `holon_session` HttpOnly cookie.
    """

    async def dependency(request: Request) -> Principal:
        authorization = request.headers.get("authorization")
        if authorization and authorization.startswith("Bearer "):
            token = authorization.removeprefix("Bearer ")
        else:
            token = request.cookies.get(COOKIE_NAME)
        if token is None:
            raise HTTPException(
                status_code=401,
                detail="authentication required (Authorization: Bearer <token> header or session cookie)",
            )
        principal = decode_token(token, secret, secrets=secrets)
        if not principal.tenant_id:
            raise HTTPException(status_code=401, detail="missing tenant_id")
        if expected_tenant_id is not None and principal.tenant_id != expected_tenant_id:
            raise HTTPException(status_code=403, detail="access denied: tenant mismatch")
        return principal

    return dependency


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_COOKIE_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def require_tenant_match(principal: Principal, resource_tenant_id: str) -> None:
    """Hard cross-tenant fence (ADR 026). Must be called on every resource
    path that already knows the resource's tenant — this helper is not
    ambient middleware; omitting it is a security bug.
    """
    if principal.tenant_id != resource_tenant_id:
        raise HTTPException(status_code=403, detail="access denied: tenant mismatch")


def require_urn_tenant_match(principal: Principal, resource_urn: str) -> None:
    """Parse `hl:{tenant}:...` and enforce `require_tenant_match`."""
    from .urn import InvalidURNError, parse as parse_urn

    try:
        parsed = parse_urn(resource_urn)
    except InvalidURNError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    require_tenant_match(principal, parsed.tenant)
