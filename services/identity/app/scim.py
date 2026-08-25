"""SCIM 2.0 provisioning server (RFC 7643/7644), mounted at `/scim/v2`."""

from __future__ import annotations

import os
import re
import secrets
from typing import Any, Optional
from urllib.parse import quote, unquote

import asyncpg
from fastapi import APIRouter, Depends, Request

from holon_common import HolonError, Principal, build_urn

from .seed import insert_principal, tenant_urn
from .status_events import enqueue_principal_status_event
from .federation import urn_safe_local_name

_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
_GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
_LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"


def _scim_bearer_token() -> str:
    raw = os.environ.get("HOLON_SCIM_BEARER_TOKEN", "")
    if raw.startswith(("env:", "vault:", "k8s:", "aws:")):
        from holon_common.secrets import get_secret

        return get_secret(raw)
    return raw


def scim_enabled() -> bool:
    return bool(os.environ.get("HOLON_SCIM_BEARER_TOKEN"))


def _scim_tenant_id() -> str:
    """Extract tenant ID for SCIM operations."""
    return os.environ.get("HOLON_SCIM_TENANT_ID") or os.environ["HOLON_TENANT_ID"]


async def require_scim_token(request: Request) -> None:
    if not scim_enabled():
        raise HolonError.not_found("ScimNotConfigured", "SCIM is not configured")
    header = request.headers.get("Authorization", "")
    token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    if not token or not secrets.compare_digest(token, _scim_bearer_token()):
        raise HolonError.unauthorized("InvalidScimToken", "invalid or missing SCIM bearer token")


router = APIRouter(dependencies=[Depends(require_scim_token)])


def _scim_error(status_code: int, detail: str) -> HolonError:
    # SCIM error bodies have their own shape (RFC 7644 §3.12); HolonError
    # already renders a JSON body + status via install_error_handlers, so
    # this only needs to pick the right status/detail — schema shape is a
    # cosmetic difference most connectors don't actually key off of.
    return HolonError(status_code=status_code, error_name="ScimError", detail=detail)



_FILTER_RE = re.compile(r'^\s*(\w+)\s+eq\s+"([^"]*)"\s*$', re.IGNORECASE)


def parse_eq_filter(filter_str: Optional[str]) -> Optional[tuple[str, str]]:
    if not filter_str:
        return None
    match = _FILTER_RE.match(filter_str)
    if not match:
        raise ValueError(f"unsupported SCIM filter (only `attr eq \"value\"` is supported): {filter_str!r}")
    return match.group(1), match.group(2)




def _user_resource(row: asyncpg.Record) -> dict[str, Any]:
    scim_id = quote(row["urn"], safe="")
    return {
        "schemas": [_USER_SCHEMA],
        "id": scim_id,
        "externalId": row["external_id"],
        "userName": row["urn"].rsplit(":", 1)[-1],
        "displayName": row["display_name"],
        "active": row["status"] == "active",
        "meta": {"resourceType": "User", "location": f"/scim/v2/Users/{scim_id}"},
    }


async def _fetch_principal_row(pool: asyncpg.Pool, urn: str) -> Optional[asyncpg.Record]:
    return await pool.fetchrow("SELECT * FROM principal WHERE urn = $1", urn)


def _require_scim_tenant(row: asyncpg.Record) -> None:
    if row["tenant_id"] != _scim_tenant_id():
        raise _scim_error(404, "User not found")


def _require_user_type(row: asyncpg.Record) -> None:
    if row["type"] != "user":
        raise _scim_error(404, "not a SCIM User (principal is a group/service-account/agent)")


def _scim_actor() -> Principal:
    tenant_id = _scim_tenant_id()
    return Principal(
        urn=build_urn(tenant_id, "global", "service-account", "scim-provisioner"),
        type="service_account",
        tenant_id=tenant_id,
        display_name="SCIM Provisioner",
    )


async def _set_status_or_fail(pool: asyncpg.Pool, urn: str, status: str) -> None:
    updated = await enqueue_principal_status_event(
        pool,
        target_principal_urn=urn,
        status=status,
        actor=_scim_actor(),
        tenant_id=_scim_tenant_id(),
        workspace_id=os.environ["HOLON_WORKSPACE_ID"],
    )
    if updated is None:
        raise _scim_error(500, "failed to persist principal status change")


@router.get("/Users")
async def list_users(request: Request, filter: Optional[str] = None) -> dict:
    pool = request.app.state.pool
    tenant_id = _scim_tenant_id()
    try:
        parsed = parse_eq_filter(filter)
    except ValueError as exc:
        raise _scim_error(400, str(exc)) from exc

    if parsed is None:
        rows = await pool.fetch("SELECT * FROM principal WHERE tenant_id = $1 AND type = 'user' ORDER BY urn", tenant_id)
    else:
        attr, value = parsed
        column = {"externalid": "external_id", "id": "urn"}.get(attr.lower())
        if column == "urn":
            value = unquote(value)
        if column:
            rows = await pool.fetch(
                f"SELECT * FROM principal WHERE tenant_id = $1 AND type = 'user' AND {column} = $2", tenant_id, value
            )
        elif attr.lower() == "username":
            urn = build_urn(tenant_id, "global", "user", value)
            row = await _fetch_principal_row(pool, urn)
            rows = [row] if row and row["type"] == "user" else []
        else:
            raise _scim_error(400, f"unsupported filter attribute: {attr!r}")

    resources = [_user_resource(row) for row in rows]
    return {"schemas": [_LIST_SCHEMA], "totalResults": len(resources), "Resources": resources}


@router.post("/Users", status_code=201)
async def create_user(request: Request, body: dict) -> dict:
    pool = request.app.state.pool
    tenant_id = _scim_tenant_id()
    user_name = body.get("userName")
    if not user_name:
        raise _scim_error(400, "userName is required")
    local_name = urn_safe_local_name(user_name, fallback="scim-user")
    display_name = (
        body.get("displayName")
        or (body.get("name") or {}).get("formatted")
        or user_name
    )
    active = body.get("active", True)

    try:
        created = await insert_principal(
            pool,
            tenant_id=tenant_id,
            type="user",
            local_name=local_name,
            display_name=str(display_name),
            external_id=body.get("externalId"),
        )
    except asyncpg.UniqueViolationError as exc:
        raise _scim_error(409, "a User with this userName already exists") from exc

    if not active:
        await _set_status_or_fail(pool, created["urn"], "disabled")
        created["status"] = "disabled"

    await request.app.state.authz.write_relationship(
        resource_type="tenant", resource_urn=tenant_urn(tenant_id), relation="member", subject_urn=created["urn"],
    )
    row = await _fetch_principal_row(pool, created["urn"])
    return _user_resource(row)


@router.get("/Users/{scim_id}")
async def get_user(request: Request, scim_id: str) -> dict:
    row = await _fetch_principal_row(request.app.state.pool, unquote(scim_id))
    if row is None:
        raise _scim_error(404, "User not found")
    _require_scim_tenant(row)
    _require_user_type(row)
    return _user_resource(row)


def _active_from_patch(body: dict) -> Optional[bool]:
    """Extract active status from SCIM PATCH payload."""
    for op in body.get("Operations", []):
        path = (op.get("path") or "").lower()
        value = op.get("value")
        if path == "active" and isinstance(value, bool):
            return value
        if not path and isinstance(value, dict) and "active" in value:
            return bool(value["active"])
    return None


@router.patch("/Users/{scim_id}")
async def patch_user(request: Request, scim_id: str, body: dict) -> dict:
    pool = request.app.state.pool
    urn = unquote(scim_id)
    row = await _fetch_principal_row(pool, urn)
    if row is None:
        raise _scim_error(404, "User not found")
    _require_scim_tenant(row)
    _require_user_type(row)

    active = _active_from_patch(body)
    if active is not None:
        new_status = "active" if active else "disabled"
        await _set_status_or_fail(pool, urn, new_status)

    row = await _fetch_principal_row(pool, urn)
    return _user_resource(row)


@router.put("/Users/{scim_id}")
async def replace_user(request: Request, scim_id: str, body: dict) -> dict:
    pool = request.app.state.pool
    urn = unquote(scim_id)
    row = await _fetch_principal_row(pool, urn)
    if row is None:
        raise _scim_error(404, "User not found")
    _require_scim_tenant(row)
    _require_user_type(row)

    display_name = body.get("displayName") or (body.get("name") or {}).get("formatted")
    if display_name:
        await pool.execute("UPDATE principal SET display_name = $2 WHERE urn = $1", urn, str(display_name))
    external_id = body.get("externalId")
    if external_id is not None:
        await pool.execute("UPDATE principal SET external_id = $2 WHERE urn = $1", urn, external_id)
    active = body.get("active")
    if active is not None:
        new_status = "active" if active else "disabled"
        await _set_status_or_fail(pool, urn, new_status)

    row = await _fetch_principal_row(pool, urn)
    return _user_resource(row)


@router.delete("/Users/{scim_id}", status_code=204, response_model=None)
async def delete_user(request: Request, scim_id: str) -> None:
    """Soft-delete SCIM user by setting status to disabled."""
    pool = request.app.state.pool
    urn = unquote(scim_id)
    row = await _fetch_principal_row(pool, urn)
    if row is None:
        raise _scim_error(404, "User not found")
    _require_scim_tenant(row)
    _require_user_type(row)
    await _set_status_or_fail(pool, urn, "disabled")




async def _group_members(pool: asyncpg.Pool, authz, group_urn: str) -> list[dict[str, Any]]:
    """Retrieve member principals for a group."""
    relationships = await authz.read_relationships(resource_type="principal", resource_urn=group_urn, relation="member")
    rows = await pool.fetch("SELECT urn, display_name FROM principal")
    from holon_common.spicedb_id import index_by_spicedb_object_id

    by_object_id = index_by_spicedb_object_id(rows)
    members = []
    for rel in relationships:
        subject = rel.get("subject", {}).get("object", {})
        if subject.get("objectType") != "principal":
            continue
        row = by_object_id.get(subject.get("objectId", ""))
        if row is None:
            continue
        members.append({"value": quote(row["urn"], safe=""), "display": row["display_name"]})
    return members


def _group_resource(row: asyncpg.Record, members: list[dict[str, Any]]) -> dict[str, Any]:
    scim_id = quote(row["urn"], safe="")
    return {
        "schemas": [_GROUP_SCHEMA],
        "id": scim_id,
        "externalId": row["external_id"],
        "displayName": row["display_name"],
        "members": members,
        "meta": {"resourceType": "Group", "location": f"/scim/v2/Groups/{scim_id}"},
    }


@router.get("/Groups")
async def list_groups(request: Request, filter: Optional[str] = None) -> dict:
    pool = request.app.state.pool
    tenant_id = _scim_tenant_id()
    try:
        parsed = parse_eq_filter(filter)
    except ValueError as exc:
        raise _scim_error(400, str(exc)) from exc

    if parsed is None:
        rows = await pool.fetch("SELECT * FROM principal WHERE tenant_id = $1 AND type = 'group' ORDER BY urn", tenant_id)
    else:
        attr, value = parsed
        if attr.lower() == "externalid":
            rows = await pool.fetch(
                "SELECT * FROM principal WHERE tenant_id = $1 AND type = 'group' AND external_id = $2", tenant_id, value
            )
        elif attr.lower() == "displayname":
            rows = await pool.fetch(
                "SELECT * FROM principal WHERE tenant_id = $1 AND type = 'group' AND display_name = $2", tenant_id, value
            )
        else:
            raise _scim_error(400, f"unsupported filter attribute: {attr!r}")

    resources = []
    for row in rows:
        members = await _group_members(pool, request.app.state.authz, row["urn"])
        resources.append(_group_resource(row, members))
    return {"schemas": [_LIST_SCHEMA], "totalResults": len(resources), "Resources": resources}


@router.post("/Groups", status_code=201)
async def create_group(request: Request, body: dict) -> dict:
    pool = request.app.state.pool
    authz = request.app.state.authz
    tenant_id = _scim_tenant_id()
    display_name = body.get("displayName")
    if not display_name:
        raise _scim_error(400, "displayName is required")
    local_name = urn_safe_local_name(display_name, fallback="scim-group")

    try:
        created = await insert_principal(
            pool, tenant_id=tenant_id, type="group", local_name=local_name,
            display_name=str(display_name), external_id=body.get("externalId"),
        )
    except asyncpg.UniqueViolationError as exc:
        raise _scim_error(409, "a Group with this displayName already exists") from exc

    await authz.write_relationship(
        resource_type="tenant", resource_urn=tenant_urn(tenant_id), relation="member", subject_urn=created["urn"],
    )
    for member in body.get("members", []) or []:
        member_urn = unquote(member.get("value", ""))
        member_row = await _fetch_principal_row(pool, member_urn) if member_urn else None
        if member_row is not None and member_row["tenant_id"] == tenant_id:
            await authz.write_relationship(
                resource_type="principal", resource_urn=created["urn"], relation="member", subject_urn=member_urn,
            )

    row = await _fetch_principal_row(pool, created["urn"])
    members = await _group_members(pool, authz, row["urn"])
    return _group_resource(row, members)


@router.get("/Groups/{scim_id}")
async def get_group(request: Request, scim_id: str) -> dict:
    pool = request.app.state.pool
    urn = unquote(scim_id)
    row = await _fetch_principal_row(pool, urn)
    if row is None or row["type"] != "group":
        raise _scim_error(404, "Group not found")
    if row["tenant_id"] != _scim_tenant_id():
        raise _scim_error(404, "Group not found")
    members = await _group_members(pool, request.app.state.authz, urn)
    return _group_resource(row, members)


@router.patch("/Groups/{scim_id}")
async def patch_group(request: Request, scim_id: str, body: dict) -> dict:
    """Apply SCIM PATCH member updates to a group."""
    pool = request.app.state.pool
    authz = request.app.state.authz
    group_urn = unquote(scim_id)
    row = await _fetch_principal_row(pool, group_urn)
    if row is None or row["type"] != "group":
        raise _scim_error(404, "Group not found")
    if row["tenant_id"] != _scim_tenant_id():
        raise _scim_error(404, "Group not found")

    for op in body.get("Operations", []):
        action = (op.get("op") or "").lower()
        path = (op.get("path") or "").lower()
        if path != "members":
            continue
        values = op.get("value") or []
        if isinstance(values, dict):
            values = [values]
        for entry in values:
            member_urn = unquote(entry.get("value", "")) if isinstance(entry, dict) else unquote(str(entry))
            if not member_urn:
                continue
            member_row = await _fetch_principal_row(pool, member_urn)
            if member_row is None or member_row["tenant_id"] != _scim_tenant_id():
                continue
            if action == "add":
                await authz.write_relationship(
                    resource_type="principal", resource_urn=group_urn, relation="member", subject_urn=member_urn,
                )
            elif action == "remove":
                await authz.delete_relationship(
                    resource_type="principal", resource_urn=group_urn, relation="member", subject_urn=member_urn,
                )

    row = await _fetch_principal_row(pool, group_urn)
    members = await _group_members(pool, authz, group_urn)
    return _group_resource(row, members)




@router.get("/ServiceProviderConfig")
async def service_provider_config() -> dict:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 200},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {"type": "oauthbearertoken", "name": "Bearer Token", "description": "Static bearer token"}
        ],
    }


@router.get("/ResourceTypes")
async def resource_types() -> dict:
    resources = [
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
            "id": "User",
            "name": "User",
            "endpoint": "/Users",
            "schema": _USER_SCHEMA,
        },
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
            "id": "Group",
            "name": "Group",
            "endpoint": "/Groups",
            "schema": _GROUP_SCHEMA,
        },
    ]
    return {"schemas": [_LIST_SCHEMA], "totalResults": len(resources), "Resources": resources}


@router.get("/Schemas")
async def schemas() -> dict:
    resources = [
        {"id": _USER_SCHEMA, "name": "User", "description": "Holon principal (type=user)"},
        {"id": _GROUP_SCHEMA, "name": "Group", "description": "Holon principal (type=group)"},
    ]
    return {"schemas": [_LIST_SCHEMA], "totalResults": len(resources), "Resources": resources}
