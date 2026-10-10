"""Identity shared helpers and process-level runtime.

`pool` / `authz` / `producer` are set once by `main.py`'s lifespan, same
pattern as Knowledge `core.py`. Route handlers live in `routers/`.
"""
from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict

import asyncpg
from fastapi import Request
from pydantic import BaseModel, Field

from holon_common import (
    HolonError,
    Principal,
    active_jwt,
    issue_token,
    make_principal_dependency,
    mark_principal_disabled,
)

from .seed import (
    get_tenant,
    workspace_urn as workspace_urn,
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


# Domain helpers live in sibling modules. Lazy re-export avoids circular imports
# (governance/access_ops/federation_login all read deps.pool / deps.authz).
_DOMAIN_EXPORTS: dict[str, tuple[str, str]] = {
    "_access_listing": ("governance", "_access_listing"),
    "_authorize_bootstrap_governance": ("governance", "_authorize_bootstrap_governance"),
    "_authorize_principal_governance": ("governance", "_authorize_principal_governance"),
    "_authorize_project_governance": ("governance", "_authorize_project_governance"),
    "_authorize_workspace_governance": ("governance", "_authorize_workspace_governance"),
    "_delete_relationship_or_reraise": ("governance", "_delete_relationship_or_reraise"),
    "_resolve_workspace_governance": ("governance", "_resolve_workspace_governance"),
    "_validate_project_relation": ("governance", "_validate_project_relation"),
    "_validate_relation": ("governance", "_validate_relation"),
    "_apply_access_change": ("access_ops", "_apply_access_change"),
    "_enqueue_permission_event": ("access_ops", "_enqueue_permission_event"),
    "_enqueue_principal_status_event": ("access_ops", "_enqueue_principal_status_event"),
    "_fanout_group_permission_event": ("access_ops", "_fanout_group_permission_event"),
    "_grant_subject_relation": ("access_ops", "_grant_subject_relation"),
    "_require_grant_target": ("access_ops", "_require_grant_target"),
    "_require_group": ("access_ops", "_require_group"),
    "_complete_federated_login": ("federation_login", "_complete_federated_login"),
}


def __getattr__(name: str):
    if name in _DOMAIN_EXPORTS:
        import importlib

        mod_name, attr = _DOMAIN_EXPORTS[name]
        module = importlib.import_module(f".{mod_name}", __name__.rsplit(".", 1)[0])
        value = getattr(module, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted({*globals(), *_DOMAIN_EXPORTS})
