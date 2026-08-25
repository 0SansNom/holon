"""Tests for the no-code REST connector's OAuth2 client_credentials
connection auth type.

Against `oauth2-idp`, a dedicated fake external IdP fixture
(`tests/fixtures/oauth2-idp/server.py`) — deliberately not Holon's own
Identity service: `holon_common.connector_safety` blocks every
platform-internal hostname (identity, connectivity, knowledge, ...) by
design, so a tenant connector must never be able to reach the
platform's own control plane. `oauth2-idp` exercises the same real
client_credentials + bearer-attach loop, just against a genuinely
separate, self-contained fake external system.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import asyncpg
import pytest
from conftest import CONNECTIVITY, IDENTITY, TENANT_ID, _unique_name

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "libs"))

from holon_sdk import HolonClient  # noqa: E402

client = HolonClient(identity_url=IDENTITY)
_request = client.request

DB_URL = f"postgresql://holon:{os.environ.get('POSTGRES_PASSWORD', 'holon12345')}@localhost:5432/holon_connectivity"

# Connectivity's own network view of oauth2-idp, not the test runner's.
OAUTH2_IDP_INTERNAL = "http://oauth2-idp:8000"
# Must match tests/fixtures/oauth2-idp/server.py's CLIENT_ID/CLIENT_SECRET.
OAUTH2_IDP_CLIENT_ID = "test-oauth2-client"
OAUTH2_IDP_CLIENT_SECRET = "test-oauth2-secret"


@pytest.fixture(scope="session")
def msmith_token() -> str:
    try:
        return client.token_for(f"hl:{TENANT_ID}:global:user:msmith")
    except TimeoutError as exc:
        pytest.fail(str(exc))


def _oauth2_expires_at(connection_name: str):
    async def _fetch():
        conn = await asyncpg.connect(dsn=DB_URL)
        try:
            return await conn.fetchval(
                "SELECT oauth2_token_expires_at FROM generic_rest_connection WHERE tenant_id = $1 AND name = $2",
                TENANT_ID, connection_name,
            )
        finally:
            await conn.close()

    return asyncio.run(_fetch())


def test_oauth2_connection_authenticates_and_syncs_real_principal_rows(msmith_token: str) -> None:
    connection_name = _unique_name("oauth2_idp_conn")
    status, connection = _request(
        "POST", f"{CONNECTIVITY}/connections", token=msmith_token,
        body={
            "name": connection_name,
            "auth_type": "oauth2_client_credentials",
            "oauth2_token_url": f"{OAUTH2_IDP_INTERNAL}/token",
            "oauth2_client_id": OAUTH2_IDP_CLIENT_ID,
            "oauth2_client_secret": OAUTH2_IDP_CLIENT_SECRET,
        },
    )
    assert status == 200, connection
    assert connection["auth_type"] == "oauth2_client_credentials", connection
    assert "oauth2_client_secret" not in connection, connection  # never echoed back
    assert connection["has_oauth2_client_secret"] is True, connection

    source_name = _unique_name("oauth2_idp_principals")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=msmith_token,
        body={"name": source_name, "base_url": f"{OAUTH2_IDP_INTERNAL}/principals", "connection_name": connection_name},
    )
    assert status == 200, registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=msmith_token, body={"dataset": source_name})
    assert status == 200, result
    assert result["row_count"] > 0, result


def test_oauth2_wrong_client_secret_fails_sync_cleanly(msmith_token: str) -> None:
    connection_name = _unique_name("oauth2_idp_conn_bad")
    status, connection = _request(
        "POST", f"{CONNECTIVITY}/connections", token=msmith_token,
        body={
            "name": connection_name,
            "auth_type": "oauth2_client_credentials",
            "oauth2_token_url": f"{OAUTH2_IDP_INTERNAL}/token",
            "oauth2_client_id": OAUTH2_IDP_CLIENT_ID,
            "oauth2_client_secret": "definitely-not-the-real-secret",
        },
    )
    assert status == 200, connection

    source_name = _unique_name("oauth2_idp_principals_bad")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=msmith_token,
        body={"name": source_name, "base_url": f"{OAUTH2_IDP_INTERNAL}/principals", "connection_name": connection_name},
    )
    assert status == 200, registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=msmith_token, body={"dataset": source_name})
    assert status == 400, result
    assert "token" in result["detail"].lower(), result


def test_oauth2_token_is_cached_between_syncs(msmith_token: str) -> None:
    connection_name = _unique_name("oauth2_idp_conn_cache")
    status, connection = _request(
        "POST", f"{CONNECTIVITY}/connections", token=msmith_token,
        body={
            "name": connection_name,
            "auth_type": "oauth2_client_credentials",
            "oauth2_token_url": f"{OAUTH2_IDP_INTERNAL}/token",
            "oauth2_client_id": OAUTH2_IDP_CLIENT_ID,
            "oauth2_client_secret": OAUTH2_IDP_CLIENT_SECRET,
        },
    )
    assert status == 200, connection

    source_name = _unique_name("oauth2_idp_principals_cache")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=msmith_token,
        body={"name": source_name, "base_url": f"{OAUTH2_IDP_INTERNAL}/principals", "connection_name": connection_name},
    )
    assert status == 200, registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=msmith_token, body={"dataset": source_name})
    assert status == 200, result
    first_expiry = _oauth2_expires_at(connection_name)
    assert first_expiry is not None

    time.sleep(1)
    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=msmith_token, body={"dataset": source_name})
    assert status == 200, result
    second_expiry = _oauth2_expires_at(connection_name)
    assert second_expiry == first_expiry, "cached token should not have been refetched within its lifetime"
