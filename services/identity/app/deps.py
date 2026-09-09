"""Identity shared helpers and process-level runtime.

`pool` / `authz` / `producer` are set once by `main.py`'s lifespan, same
pattern as Knowledge `core.py`. Route handlers live in `routers/`.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from collections import OrderedDict

import asyncpg
from fastapi import Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from holon_common import (
    HolonError,
    EventActor,
    EventEnvelope,
    Principal,
    active_jwt,
    issue_token,
    make_principal_dependency,
    mark_principal_disabled,
    outbox,
    set_session_cookie,
)
from holon_common.audit import emit_audit

from .seed import (
    VALID_PROJECT_RELATIONS,
    VALID_WORKSPACE_RELATIONS,
    get_tenant,
    get_workspace,
    insert_principal,
    list_projects,
    list_workspaces,
    tenant_urn,
    workspace_urn,
)

SERVICE_NAME = "identity-platform"
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

pool = None
authz = None
producer = None

_AUTH_ATTEMPTS: OrderedDict[str, list[float]] = OrderedDict()
_AUTH_WINDOW_SECONDS = 60.0
_AUTH_MAX_ATTEMPTS = 10
_AUTH_ATTEMPTS_MAX_KEYS = 4096
_FEDERATED_ERROR_NAME = {"oidc": "OidcError", "saml": "SamlError"}

_base_principal = make_principal_dependency(JWT_SECRET, secrets=JWT_SECRETS, check_disabled_denylist=False)


class TokenRequest(BaseModel):
    principal_urn: str
    client_secret: str


class AccessRequest(BaseModel):
    relation: str
    # When omitted: bootstrap workspace for the bootstrap tenant, otherwise
    # the first workspace in the target tenant the caller can approve.
    workspace_id: str | None = None


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


class GroupMemberRequest(BaseModel):
    principal_urn: str


class CreateProjectRequest(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
        description="URL-safe project name",
    )


async def current_principal(request: Request) -> Principal:
    principal = await _base_principal(request)
    from .token_revocation import is_jti_revoked_in_db

    if principal.jti and await is_jti_revoked_in_db(pool, principal.jti):
        raise HolonError.unauthorized("TokenRevoked", "token has been revoked")
    row = await pool.fetchrow("SELECT status, tenant_id FROM principal WHERE urn = $1", principal.urn)
    if row is None or row["status"] != "active":
        mark_principal_disabled(principal.urn)
        raise HolonError.unauthorized("PrincipalDisabled", "principal is disabled")
    tenant = await get_tenant(pool, row["tenant_id"])
    if tenant is None or tenant["status"] != "active":
        raise HolonError.forbidden("TenantDisabled", "tenant is disabled")
    return principal


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
    row = await pool.fetchrow("SELECT * FROM principal WHERE urn = $1", urn)
    if row is None or row["status"] != "active":
        raise HolonError.unauthorized('InvalidCredentials', "invalid principal_urn or client_secret")
    tenant = await get_tenant(pool, row["tenant_id"])
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
    target = await _fetch_principal(pool, urn)
    if target is None:
        raise HolonError.not_found('PrincipalNotFound', f"unknown principal: {urn}")
    if target.tenant_id != tenant_id:
        raise HolonError.invalid_argument('CrossTenantPrincipal', "principal belongs to another tenant")
    return target


async def _authorize_bootstrap_governance(principal: Principal) -> None:
    """Authorize tenant creation on the bootstrap workspace."""
    decision = await authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=workspace_urn(TENANT_ID, WORKSPACE_ID),
        permission="approve",
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


async def _authorize_workspace_governance(principal: Principal, tenant_id: str, workspace_id: str) -> str:
    ws = await get_workspace(pool, workspace_id)
    if ws is None or ws["tenant_id"] != tenant_id:
        raise HolonError.not_found('WorkspaceNotFound', f"unknown workspace: {workspace_id}")
    if ws["status"] != "active":
        raise HolonError.invalid_argument('WorkspaceDisabled', "workspace is disabled")
    w_urn = workspace_urn(tenant_id, workspace_id)
    decision = await authz.authorize(
        principal, resource_type="workspace", resource_urn=w_urn, permission="approve"
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)
    return w_urn


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
        await authz.delete_relationship(
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

    tenant = await get_tenant(pool, tenant_id)
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

    row = await pool.fetchrow(f"SELECT * FROM principal WHERE {lookup_column} = $1", external_id)
    if row is None:
        try:
            created = await insert_principal(
                pool,
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
        await authz.write_relationship(
            resource_type="tenant",
            resource_urn=tenant_urn(tenant_id),
            relation="member",
            subject_urn=created["urn"],
        )
        row = await pool.fetchrow("SELECT * FROM principal WHERE urn = $1", created["urn"])
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
    for ws in await list_workspaces(pool, principal.tenant_id):
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
        ws = await get_workspace(pool, workspace_id)
        if ws is None or ws["tenant_id"] != principal.tenant_id:
            continue
        w_urn = workspace_urn(principal.tenant_id, workspace_id)
        await authz.write_relationship(
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


async def _require_group(group_urn: str) -> Principal:
    group = await _fetch_principal(pool, group_urn)
    if group is None:
        raise HolonError.not_found("PrincipalNotFound", f"unknown principal: {group_urn}")
    if group.type != "group":
        raise HolonError.invalid_argument("NotAGroup", f"{group_urn} is not a group", urn=group_urn)
    return group


async def _authorize_principal_governance(principal: Principal, tenant_id: str) -> None:
    workspaces = await list_workspaces(pool, tenant_id)
    if not workspaces:
        await _authorize_bootstrap_governance(principal)
    else:
        await _authorize_workspace_governance(principal, tenant_id, workspaces[0]["workspace_id"])


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
    workspaces = await list_workspaces(pool, tenant_id)
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
    relationships = await authz.read_relationships(resource_type=resource_type, resource_urn=resource_urn)
    rows = await pool.fetch("SELECT * FROM principal")
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
    async with pool.acquire() as conn:
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
        pool,
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


async def _authorize_project_governance(principal: Principal, project_name: str) -> str:
    """Authorize project governance permissions."""
    projects = await list_projects(pool, principal.tenant_id)
    project = next((p for p in projects if p["name"] == project_name), None)
    if project is None:
        raise HolonError.not_found('ProjectNotFound', f"unknown project: {project_name}")
    urn = project["urn"]
    decision = await authz.authorize(principal, resource_type="project", resource_urn=urn, permission="approve")
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)
    return urn


def _validate_project_relation(relation: str) -> None:
    if relation not in VALID_PROJECT_RELATIONS:
        raise HolonError.invalid_argument('InvalidProjectRelation', f"invalid relation: {relation!r} (must be one of {sorted(VALID_PROJECT_RELATIONS)})",
        )

