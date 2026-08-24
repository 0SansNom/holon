"""Tests for Identity Auth."""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from http.cookies import SimpleCookie

import asyncpg

from conftest import IDENTITY, TENANT_ID


JDOE_URN = f"hl:{TENANT_ID}:global:user:jdoe"

DB_URL = f"postgresql://holon:{os.environ.get('POSTGRES_PASSWORD', 'holon12345')}@localhost:5432/holon_identity"


def _raw_request(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict, object]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{IDENTITY}{path}", data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read()), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()), exc.headers


def test_login_cookie_auth_whoami_and_logout() -> None:
    status, body, headers = _raw_request(
        "POST",
        "/login",
        body={"principal_urn": JDOE_URN, "client_secret": "jdoe-dev-secret"},
    )
    assert status == 200, body

    set_cookie = headers.get("Set-Cookie")
    assert set_cookie
    parsed = SimpleCookie()
    parsed.load(set_cookie)
    session = parsed["holon_session"]
    assert session["httponly"]
    assert session["secure"]
    assert session["samesite"].lower() == "strict"

    cookie_header = {"Cookie": f"holon_session={session.value}"}
    status, principal, _ = _raw_request("GET", "/whoami", headers=cookie_header)
    assert status == 200, principal
    assert principal["urn"] == JDOE_URN

    status, body, headers = _raw_request("POST", "/logout", headers=cookie_header)
    assert status == 200, body
    assert "Max-Age=0" in headers.get("Set-Cookie", "")

    status, body, _ = _raw_request("GET", "/whoami")
    assert status == 401, body


def test_login_rejects_invalid_secret() -> None:
    status, body, headers = _raw_request(
        "POST",
        "/login",
        body={"principal_urn": JDOE_URN, "client_secret": "wrong"},
    )
    assert status == 401, body
    assert headers.get("Set-Cookie") is None


def test_create_principal_without_client_secret_gets_a_random_one(msmith_token) -> None:
    """A `POST /principals` that omits `client_secret` must not fall back to
    a name-derivable secret (`{local_name}-dev-secret`) — it should mint a
    random one, returned once, that's the only way to authenticate as the
    new principal."""
    local_name = f"randsecret_{int(time.time() * 1000)}"
    status, created, _ = _raw_request(
        "POST",
        "/principals",
        body={"tenant_id": TENANT_ID, "type": "user", "local_name": local_name, "display_name": "Random Secret Test"},
        headers={"Authorization": f"Bearer {msmith_token}"},
    )
    assert status == 201, created
    secret = created["client_secret"]
    assert secret != f"{local_name}-dev-secret"

    status, token_body, _ = _raw_request(
        "POST", "/token", body={"principal_urn": created["urn"], "client_secret": secret}
    )
    assert status == 200, token_body

    status, guess_body, _ = _raw_request(
        "POST",
        "/token",
        body={"principal_urn": created["urn"], "client_secret": f"{local_name}-dev-secret"},
    )
    assert status == 401, guess_body


def test_legacy_plaintext_secret_authenticates_once_then_migrates_to_hash(msmith_token) -> None:
    """A principal row left over from before hashing (plaintext
    `client_secret`, no `client_secret_hash`) must still authenticate, and
    the first successful auth should migrate it to a hash in place."""
    local_name = f"legacysecret_{int(time.time() * 1000)}"
    urn = f"hl:{TENANT_ID}:global:user:{local_name}"
    plaintext_secret = "legacy-plaintext-secret"

    async def _seed_legacy_row() -> None:
        conn = await asyncpg.connect(dsn=DB_URL)
        try:
            await conn.execute(
                """
                INSERT INTO principal (urn, type, tenant_id, display_name, client_secret, client_secret_hash, status)
                VALUES ($1, 'user', $2, 'Legacy Secret Test', $3, NULL, 'active')
                """,
                urn,
                TENANT_ID,
                plaintext_secret,
            )
        finally:
            await conn.close()

    async def _fetch_row() -> asyncpg.Record:
        conn = await asyncpg.connect(dsn=DB_URL)
        try:
            return await conn.fetchrow(
                "SELECT client_secret, client_secret_hash FROM principal WHERE urn = $1", urn
            )
        finally:
            await conn.close()

    async def _cleanup() -> None:
        conn = await asyncpg.connect(dsn=DB_URL)
        try:
            await conn.execute("DELETE FROM principal WHERE urn = $1", urn)
        finally:
            await conn.close()

    asyncio.run(_seed_legacy_row())
    try:
        status, token_body, _ = _raw_request(
            "POST", "/token", body={"principal_urn": urn, "client_secret": plaintext_secret}
        )
        assert status == 200, token_body

        row = asyncio.run(_fetch_row())
        assert row["client_secret"] is None
        assert row["client_secret_hash"] is not None

        status, token_body, _ = _raw_request(
            "POST", "/token", body={"principal_urn": urn, "client_secret": plaintext_secret}
        )
        assert status == 200, token_body
    finally:
        asyncio.run(_cleanup())
