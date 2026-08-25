"""Identity primitives.

`Principal` and its JWT encoding form the identity primitives.
`holon_common.authz.PermissionClient` handles authorization decisions.

**Algorithms.** `HOLON_JWT_ALG` is `HS256` (default, shared secret) or
`RS256` (asymmetric). For RS256, issuers use `HOLON_JWT_PRIVATE_KEYS`
and every service verifies with `HOLON_JWT_PUBLIC_KEYS` (JSON
`{"kid":"-----BEGIN ...-----\\n..."}` or the same `kid:value,...` form
as HS256 when values have no commas).

**Key rotation.** Under HS256, `HOLON_JWT_SECRETS` is a `kid:secret` map
(`kid1:secretA,kid2:secretB` or JSON). `HOLON_JWT_SECRET` (singular)
remains supported as the single active key with kid `default`.
Services boot via `active_jwt()` and pass `kid`+`secrets` into `issue_token`
so the active kid is stamped; `decode_token` looks up the header kid then
verifies — 401 if kid missing/unknown.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Iterable, Mapping, Optional

import jwt
from fastapi import Request, Response
from pydantic import BaseModel, Field

from .errors import HolonError

COOKIE_NAME = "holon_session"
SESSION_COOKIE_TTL_SECONDS = 3600
SUPPORTED_JWT_ALGS = frozenset({"HS256", "RS256"})

# Process-local denylists populated from identity.principal.status_changed /
# identity.token.revoked (and Identity's own writes). JWT decode still trusts
# exp; these close the "disabled principal / logged-out session, live JWT"
# window. Other services hydrate from Identity's snapshot on boot so a
# restart does not revive revoked tokens. Identity also re-reads Postgres.
_disabled_principal_urns: set[str] = set()
_revoked_jtis: set[str] = set()


def mark_principal_disabled(urn: str) -> None:
    if urn:
        _disabled_principal_urns.add(urn)


def mark_principal_enabled(urn: str) -> None:
    _disabled_principal_urns.discard(urn)


def is_principal_disabled(urn: str) -> bool:
    return urn in _disabled_principal_urns


def mark_jti_revoked(jti: str) -> None:
    if jti:
        _revoked_jtis.add(jti)


def is_jti_revoked(jti: str) -> bool:
    return bool(jti) and jti in _revoked_jtis


def replace_disabled_principal_urns(urns: Iterable[str]) -> None:
    _disabled_principal_urns.clear()
    _disabled_principal_urns.update(u for u in urns if u)


def replace_revoked_jtis(jtis: Iterable[str]) -> None:
    _revoked_jtis.clear()
    _revoked_jtis.update(j for j in jtis if j)


def reset_revocation_state() -> None:
    """Test helper — empty both in-memory denylists."""
    _disabled_principal_urns.clear()
    _revoked_jtis.clear()


def apply_principal_status_payload(payload: Mapping[str, Any]) -> None:
    """Apply `identity.principal.status_changed` to the process-local denylist.

    Enable must clear the urn on every replica that received the disable
    event — unique per-process Kafka groups (see `principal_status`) fan
    the event out so this discard actually runs everywhere.
    """
    urn = str(payload.get("principal_urn") or "")
    if not urn:
        return
    if payload.get("status") == "active":
        mark_principal_enabled(urn)
    else:
        mark_principal_disabled(urn)


def apply_token_revoked_payload(payload: Mapping[str, Any]) -> None:
    """Apply `identity.token.revoked` to the process-local jti denylist."""
    mark_jti_revoked(str(payload.get("jti") or ""))


class Principal(BaseModel):
    urn: str
    type: str  # user | service_account | agent
    tenant_id: str
    display_name: str
    on_behalf_of: Optional[str] = None
    country: Optional[str] = None
    jti: Optional[str] = Field(default=None, exclude=True)


def jwt_algorithm() -> str:
    alg = (os.environ.get("HOLON_JWT_ALG") or "HS256").strip().upper() or "HS256"
    if alg not in SUPPORTED_JWT_ALGS:
        raise ValueError(f"unsupported HOLON_JWT_ALG={alg!r} (supported: {sorted(SUPPORTED_JWT_ALGS)})")
    return alg


def _looks_like_secret_ref(value: str) -> bool:
    return value.startswith(("env:", "vault:", "k8s:", "aws:"))


def _resolve_secret_material(value: str) -> str:
    """Resolve ``vault:…`` / ``env:…`` / … refs; leave literal PEMs/secrets as-is."""
    value = value.strip()
    if _looks_like_secret_ref(value):
        from .secrets import get_secret

        return get_secret(value).replace("\\n", "\n")
    return value.replace("\\n", "\n")


def _parse_key_map(raw: str, *, what: str) -> dict[str, str]:
    raw = raw.strip()
    if raw.startswith("{"):
        mapping = {str(k): _resolve_secret_material(str(v)) for k, v in json.loads(raw).items()}
    else:
        mapping = {}
        for part in raw.split(","):
            kid, _, secret = part.partition(":")
            if not kid or not secret:
                raise ValueError(f"invalid {what} entry: {part!r}")
            # kid:vault:path#key — rejoin after first colon for the value side
            mapping[kid.strip()] = _resolve_secret_material(secret.strip())
    if not mapping:
        raise ValueError(f"{what} is empty")
    return mapping


def load_jwt_secrets() -> tuple[dict[str, str], str]:
    """Returns (kid→signing material, active_kid).

    HS256: HMAC secrets. RS256: private PEM keys (`HOLON_JWT_PRIVATE_KEYS`
    preferred; `HOLON_JWT_SECRETS` accepted as an alias for minting pods).

    Values may be secret-store refs (``vault:path#key``, ``env:NAME``, …).
    """
    alg = jwt_algorithm()
    if alg == "RS256":
        multi = (os.environ.get("HOLON_JWT_PRIVATE_KEYS") or os.environ.get("HOLON_JWT_SECRETS") or "").strip()
        if multi:
            if _looks_like_secret_ref(multi) and not multi.strip().startswith("{"):
                multi = _resolve_secret_material(multi)
            mapping = _parse_key_map(multi, what="HOLON_JWT_PRIVATE_KEYS")
            active = os.environ.get("HOLON_JWT_ACTIVE_KID") or next(iter(mapping))
            if active not in mapping:
                raise ValueError(f"HOLON_JWT_ACTIVE_KID {active!r} not in private key map")
            return mapping, active
        singular = (os.environ.get("HOLON_JWT_PRIVATE_KEY") or "").strip()
        if not singular:
            raise RuntimeError(
                "RS256 requires HOLON_JWT_PRIVATE_KEYS (or HOLON_JWT_PRIVATE_KEY) on minting services"
            )
        return {"default": _resolve_secret_material(singular)}, "default"

    multi = (os.environ.get("HOLON_JWT_SECRETS") or "").strip() or None
    if multi:
        if _looks_like_secret_ref(multi) and not multi.strip().startswith("{"):
            multi = _resolve_secret_material(multi)
        mapping = _parse_key_map(multi, what="HOLON_JWT_SECRETS")
        active = os.environ.get("HOLON_JWT_ACTIVE_KID") or next(iter(mapping))
        if active not in mapping:
            raise ValueError(f"HOLON_JWT_ACTIVE_KID {active!r} not in HOLON_JWT_SECRETS")
        return mapping, active
    singular = os.environ.get("HOLON_JWT_SECRET")
    if not singular:
        raise RuntimeError("HOLON_JWT_SECRET or HOLON_JWT_SECRETS required")
    return {"default": _resolve_secret_material(singular)}, "default"


def load_jwt_verify_keys() -> dict[str, str]:
    """kid→verify material (HMAC secret or public PEM)."""
    alg = jwt_algorithm()
    if alg == "RS256":
        multi = (os.environ.get("HOLON_JWT_PUBLIC_KEYS") or "").strip()
        if multi:
            if _looks_like_secret_ref(multi) and not multi.strip().startswith("{"):
                multi = _resolve_secret_material(multi)
            return _parse_key_map(multi, what="HOLON_JWT_PUBLIC_KEYS")
        singular = (os.environ.get("HOLON_JWT_PUBLIC_KEY") or "").strip()
        if singular:
            return {"default": _resolve_secret_material(singular)}
        raise RuntimeError("RS256 requires HOLON_JWT_PUBLIC_KEYS (or HOLON_JWT_PUBLIC_KEY) on every service")
    secrets, _ = load_jwt_secrets()
    return secrets


def active_jwt() -> tuple[str, str, dict[str, str]]:
    """Returns `(active_sign_key, active_kid, sign_keys_map)` for service boot.

    Pass `sign_keys_map` to `issue_token(..., secrets=...)`. Prefer
    `load_jwt_verify_keys()` for `decode_token` / `make_principal_dependency`
    under RS256 so verify pods need no private key — call sites that still
    pass the sign map into decode continue to work under HS256 only.
    """
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
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + ttl_seconds,
    }
    alg = jwt_algorithm()
    headers = {"kid": kid} if kid else None
    sign_key = secrets[kid] if secrets and kid else secret
    return jwt.encode(payload, sign_key, algorithm=alg, headers=headers)


def decode_token(token: str, secret: str, *, secrets: Optional[dict[str, str]] = None) -> Principal:
    alg = jwt_algorithm()
    try:
        if alg == "RS256":
            verify_map = load_jwt_verify_keys()
        elif secrets:
            verify_map = secrets
        else:
            verify_map = None

        if verify_map is not None:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid") or "default"
            if kid not in verify_map:
                raise HolonError.unauthorized("UnknownJwtKid", f"unknown jwt kid: {kid}", kid=kid)
            verify_key = verify_map[kid]
        else:
            verify_key = secret
        payload = jwt.decode(token, verify_key, algorithms=[alg])
    except HolonError:
        raise
    except jwt.PyJWTError as exc:
        raise HolonError.unauthorized("InvalidToken", f"invalid token: {exc}") from exc
    return Principal(
        urn=payload["sub"],
        type=payload["type"],
        tenant_id=payload["tenant_id"],
        display_name=payload["display_name"],
        on_behalf_of=payload.get("on_behalf_of"),
        country=payload.get("country"),
        jti=payload.get("jti"),
    )


def make_principal_dependency(
    secret: str,
    *,
    expected_tenant_id: Optional[str] = None,
    secrets: Optional[dict[str, str]] = None,
    check_disabled_denylist: bool = True,
):
    """Builds a FastAPI dependency that resolves the current Principal from
    either the Authorization header or the `holon_session` HttpOnly cookie.

    Identity passes `check_disabled_denylist=False`: Postgres is the source
    of truth there, and the in-memory set is not shared across replicas.
    """

    async def dependency(request: Request) -> Principal:
        authorization = request.headers.get("authorization")
        if authorization and authorization.startswith("Bearer "):
            token = authorization.removeprefix("Bearer ")
        else:
            token = request.cookies.get(COOKIE_NAME)
        if token is None:
            raise HolonError.unauthorized(
                "AuthenticationRequired",
                "authentication required (Authorization: Bearer <token> header or session cookie)",
            )
        # Under RS256, decode_token ignores `secrets` and loads public keys
        # from the environment so verify-only pods need no private key.
        principal = decode_token(token, secret, secrets=secrets)
        if principal.jti and is_jti_revoked(principal.jti):
            raise HolonError.unauthorized("TokenRevoked", "token has been revoked")
        if check_disabled_denylist and is_principal_disabled(principal.urn):
            raise HolonError.unauthorized("PrincipalDisabled", "principal is disabled")
        if not principal.tenant_id:
            raise HolonError.unauthorized("MissingTenantId", "missing tenant_id")
        if expected_tenant_id is not None and principal.tenant_id != expected_tenant_id:
            raise HolonError.forbidden(
                "TenantMismatch",
                "access denied: tenant mismatch",
                expected_tenant_id=expected_tenant_id,
                principal_tenant_id=principal.tenant_id,
            )
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
    # Must match set_session_cookie flags — browsers key cookies on
    # Secure/SameSite and ignore a deletion that omits them.
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        secure=True,
        samesite="strict",
    )


def require_tenant_match(principal: Principal, resource_tenant_id: str) -> None:
    """Hard cross-tenant fence (ADR 026). Must be called on every resource
    path that already knows the resource's tenant — this helper is not
    ambient middleware; omitting it is a security bug.
    """
    if principal.tenant_id != resource_tenant_id:
        raise HolonError.forbidden(
            "TenantMismatch",
            "access denied: tenant mismatch",
            expected_tenant_id=resource_tenant_id,
            principal_tenant_id=principal.tenant_id,
        )


def require_urn_tenant_match(principal: Principal, resource_urn: str) -> None:
    """Parse `hl:{tenant}:...` and enforce `require_tenant_match`."""
    from .urn import InvalidURNError, parse as parse_urn

    try:
        parsed = parse_urn(resource_urn)
    except InvalidURNError as exc:
        raise HolonError.invalid_argument("InvalidUrn", str(exc), resource_urn=resource_urn) from exc
    require_tenant_match(principal, parsed.tenant)
