"""Identity primitives.

`Principal` and its JWT encoding form the identity primitives.
`holon_common.authz.PermissionClient` handles authorization decisions.
"""

from __future__ import annotations

import time
from typing import Optional

import jwt
from fastapi import Header, HTTPException, status
from pydantic import BaseModel


class Principal(BaseModel):
    urn: str
    type: str  # user | service_account | agent
    tenant_id: str
    display_name: str
    on_behalf_of: Optional[str] = None
    country: Optional[str] = None


def issue_token(principal: Principal, secret: str, ttl_seconds: int = 3600) -> str:
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
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(token: str, secret: str) -> Principal:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
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


def make_principal_dependency(secret: str):
    """Builds a FastAPI dependency that resolves the current Principal from
    the Authorization header and enforces tenant_id.
    """

    async def dependency(authorization: str = Header(...)) -> Principal:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Authorization: Bearer <token> header required")
        token = authorization.removeprefix("Bearer ")
        principal = decode_token(token, secret)
        if not principal.tenant_id:
            raise HTTPException(status_code=401, detail="missing tenant_id")
        return principal

    return dependency


def require_tenant_match(principal: Principal, resource_tenant_id: str) -> None:
    """Minimal access guard: rejects any cross-tenant access.
    """
    if principal.tenant_id != resource_tenant_id:
        raise HTTPException(status_code=403, detail="access denied: tenant mismatch")
