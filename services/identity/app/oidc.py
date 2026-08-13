"""OIDC authorization-code + PKCE client (ADR 026 Phase 2).

Enabled when HOLON_OIDC_ISSUER is set. The deployer brings the IdP;
we only implement the client. Claim → tenant mapping via
HOLON_OIDC_TENANT_CLAIM (default `tenant_id`) or group prefix
HOLON_OIDC_TENANT_GROUP_PREFIX (e.g. `tenant:` → group `tenant:filiale-a`).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from typing import Any, Optional
from urllib.parse import urlencode

import asyncpg

logger = logging.getLogger("identity.oidc")


def oidc_enabled() -> bool:
    return bool(os.environ.get("HOLON_OIDC_ISSUER"))


def _client_secret() -> str:
    """Resolve OIDC client secret via secret provider when configured as a ref."""
    raw = os.environ.get("HOLON_OIDC_CLIENT_SECRET", "")
    if raw.startswith(("env:", "vault:", "k8s:", "aws:")):
        from holon_common.secrets import get_secret

        return get_secret(raw)
    return raw


async def discover() -> dict[str, Any]:
    import httpx

    issuer = os.environ["HOLON_OIDC_ISSUER"].rstrip("/")
    url = f"{issuer}/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def build_authorize_url(pool: asyncpg.Pool, *, redirect_uri: str) -> dict[str, str]:
    """Build OIDC authorization URL with PKCE state stored in DB."""
    meta = await discover()
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM oidc_pending_state WHERE created_at < now() - interval '10 minutes'")
            await conn.execute(
                "INSERT INTO oidc_pending_state (state, verifier, redirect_uri) VALUES ($1, $2, $3)",
                state, verifier, redirect_uri,
            )
    params = {
        "client_id": os.environ["HOLON_OIDC_CLIENT_ID"],
        "response_type": "code",
        "scope": os.environ.get("HOLON_OIDC_SCOPES", "openid profile email groups"),
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return {"authorize_url": f"{meta['authorization_endpoint']}?{urlencode(params)}", "state": state}


async def exchange_code(pool: asyncpg.Pool, *, code: str, state: str) -> dict[str, Any]:
    """Exchange code for tokens and return **userinfo claims only**.

    We never decode an unverified `id_token`. Trust comes from the token
    endpoint (TLS + client_secret + PKCE) then the userinfo endpoint with
    the access_token. Providers without userinfo are rejected — configure
    JWKS verification separately if you need id_token-only IdPs later.
    """
    import httpx

    pending = await pool.fetchrow("DELETE FROM oidc_pending_state WHERE state = $1 RETURNING verifier, redirect_uri", state)
    if pending is None:
        raise ValueError("invalid or expired OIDC state")
    meta = await discover()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": pending["redirect_uri"],
        "client_id": os.environ["HOLON_OIDC_CLIENT_ID"],
        "client_secret": _client_secret(),
        "code_verifier": pending["verifier"],
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(meta["token_endpoint"], data=data)
        response.raise_for_status()
        tokens = response.json()
        access_token = tokens.get("access_token")
        userinfo_url = meta.get("userinfo_endpoint")
        if not access_token or not userinfo_url:
            raise ValueError(
                "OIDC provider must return access_token and advertise userinfo_endpoint "
                "(unsigned id_token decoding is not supported)"
            )
        ui = await client.get(userinfo_url, headers={"Authorization": f"Bearer {access_token}"})
        ui.raise_for_status()
        claims = ui.json()
    if not claims.get("sub"):
        raise ValueError("OIDC userinfo missing required sub claim")
    return claims


def tenant_from_claims(claims: dict[str, Any], *, default_tenant: str) -> str:
    claim_name = os.environ.get("HOLON_OIDC_TENANT_CLAIM", "tenant_id")
    if claim_name in claims and claims[claim_name]:
        return str(claims[claim_name])
    prefix = os.environ.get("HOLON_OIDC_TENANT_GROUP_PREFIX", "tenant:")
    groups = claims.get("groups") or claims.get("roles") or []
    if isinstance(groups, str):
        groups = [groups]
    for group in groups:
        if isinstance(group, str) and group.startswith(prefix):
            return group[len(prefix) :]
    return default_tenant


def local_name_from_claims(claims: dict[str, Any]) -> str:
    sub = str(claims.get("sub") or "unknown")
    # URN-safe local segment
    safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in sub)[:64]
    return safe or "oidc-user"


def display_name_from_claims(claims: dict[str, Any]) -> str:
    return str(claims.get("name") or claims.get("email") or claims.get("preferred_username") or claims.get("sub") or "OIDC User")


def groups_from_claims(claims: dict[str, Any]) -> list[str]:
    groups = claims.get("groups") or claims.get("roles") or []
    if isinstance(groups, str):
        return [groups]
    return [str(g) for g in groups]
