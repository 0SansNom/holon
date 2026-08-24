"""Tests for the SCIM 2.0 provisioning server (/scim/v2)."""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import asyncpg

from conftest import IDENTITY, TENANT_ID

DB_URL = f"postgresql://holon:{os.environ.get('POSTGRES_PASSWORD', 'holon12345')}@localhost:5432/holon_identity"
SCIM_TOKEN = os.environ.get("HOLON_SCIM_BEARER_TOKEN", "")


def _scim_request(method: str, path: str, *, body: dict | None = None, token: str | None = SCIM_TOKEN) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    encoded_path = urllib.parse.quote(path, safe="/?=&")
    request = urllib.request.Request(f"{IDENTITY}/scim/v2{encoded_path}", data=data, method=method)
    request.add_header("Content-Type", "application/scim+json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else {})


def _principal_status(urn: str) -> str | None:
    async def _fetch() -> str | None:
        conn = await asyncpg.connect(dsn=DB_URL)
        try:
            row = await conn.fetchrow("SELECT status FROM principal WHERE urn = $1", urn)
            return row["status"] if row else None
        finally:
            await conn.close()

    return asyncio.run(_fetch())


def _delete_principal(urn: str) -> None:
    async def _run() -> None:
        conn = await asyncpg.connect(dsn=DB_URL)
        try:
            await conn.execute("DELETE FROM principal WHERE urn = $1", urn)
        finally:
            await conn.close()

    asyncio.run(_run())


def test_scim_requires_bearer_token() -> None:
    status, body = _scim_request("GET", "/ServiceProviderConfig", token=None)
    assert status == 401, body


def test_scim_rejects_wrong_bearer_token() -> None:
    status, body = _scim_request("GET", "/ServiceProviderConfig", token="not-the-real-token")
    assert status == 401, body


def test_service_provider_config() -> None:
    status, body = _scim_request("GET", "/ServiceProviderConfig")
    assert status == 200, body
    assert body["patch"]["supported"] is True
    assert body["filter"]["supported"] is True


def test_resource_types_and_schemas() -> None:
    status, body = _scim_request("GET", "/ResourceTypes")
    assert status == 200, body
    names = {r["name"] for r in body["Resources"]}
    assert {"User", "Group"} <= names

    status, body = _scim_request("GET", "/Schemas")
    assert status == 200, body


def test_user_lifecycle_create_filter_patch_deactivate() -> None:
    local = f"scimuser{int(time.time() * 1000)}"
    urn = f"hl:{TENANT_ID}:global:user:{local}"
    external_id = f"ext-{local}"
    try:
        status, created = _scim_request(
            "POST", "/Users", body={"userName": local, "externalId": external_id, "active": True}
        )
        assert status == 201, created
        scim_id = created["id"]
        assert created["active"] is True
        assert created["externalId"] == external_id
        assert "client_secret" not in created

        status, listed = _scim_request("GET", f'/Users?filter=userName eq "{local}"')
        assert status == 200, listed
        assert listed["totalResults"] == 1
        assert listed["Resources"][0]["id"] == scim_id

        status, by_ext = _scim_request("GET", f'/Users?filter=externalId eq "{external_id}"')
        assert status == 200, by_ext
        assert by_ext["totalResults"] == 1

        assert _principal_status(urn) == "active"

        status, patched = _scim_request(
            "PATCH", f"/Users/{scim_id}",
            body={"Operations": [{"op": "replace", "path": "active", "value": False}]},
        )
        assert status == 200, patched
        assert patched["active"] is False
        assert _principal_status(urn) == "disabled"

        # Okta's path-less PATCH shape reactivates it again.
        status, patched = _scim_request(
            "PATCH", f"/Users/{scim_id}",
            body={"Operations": [{"op": "replace", "value": {"active": True}}]},
        )
        assert status == 200, patched
        assert patched["active"] is True
        assert _principal_status(urn) == "active"
    finally:
        _delete_principal(urn)


def test_create_user_conflict_on_duplicate_username() -> None:
    local = f"scimdup{int(time.time() * 1000)}"
    urn = f"hl:{TENANT_ID}:global:user:{local}"
    try:
        status, _ = _scim_request("POST", "/Users", body={"userName": local})
        assert status == 201
        status, body = _scim_request("POST", "/Users", body={"userName": local})
        assert status == 409, body
    finally:
        _delete_principal(urn)


def test_group_lifecycle_create_with_member_and_patch() -> None:
    user_local = f"scimgroupmember{int(time.time() * 1000)}"
    user_urn = f"hl:{TENANT_ID}:global:user:{user_local}"
    group_local = f"SCIM Test Group {int(time.time() * 1000)}"
    group_urn_guess = None
    try:
        status, member = _scim_request("POST", "/Users", body={"userName": user_local})
        assert status == 201, member
        member_id = member["id"]

        status, group = _scim_request(
            "POST", "/Groups", body={"displayName": group_local, "members": [{"value": member_id}]}
        )
        assert status == 201, group
        group_id = group["id"]
        from urllib.parse import unquote

        group_urn_guess = unquote(group_id)
        assert len(group["members"]) == 1
        assert group["members"][0]["value"] == member_id

        status, fetched = _scim_request("GET", f"/Groups/{group_id}")
        assert status == 200, fetched
        assert len(fetched["members"]) == 1

        status, patched = _scim_request(
            "PATCH", f"/Groups/{group_id}",
            body={"Operations": [{"op": "remove", "path": "members", "value": [{"value": member_id}]}]},
        )
        assert status == 200, patched
        assert patched["members"] == []
    finally:
        _delete_principal(user_urn)
        if group_urn_guess:
            _delete_principal(group_urn_guess)


def test_unsupported_filter_grammar_is_rejected() -> None:
    status, body = _scim_request("GET", '/Users?filter=userName co "partial"')
    assert status == 400, body
