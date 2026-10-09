"""Intelligence shared helpers and process-level runtime.

`pool` / `authz` / `qdrant` / `llm` / `embedder` / `s3` are set by `main.py`'s lifespan.
Route handlers live in `routers/`.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from holon_common import (
    HolonError,
    Principal,
    active_jwt,
    build_urn,
    is_production,
    issue_token,
    make_principal_dependency,
)
from holon_common.spicedb_id import spicedb_object_id

from . import spend_limits
from .spend_limits import SpendLimitExceeded

logger = logging.getLogger("intelligence.authz")

SERVICE_NAME = "intelligence-platform"

TENANT_ID = os.environ["HOLON_TENANT_ID"]
WORKSPACE_ID = os.environ["HOLON_WORKSPACE_ID"]
JWT_SECRET, JWT_ACTIVE_KID, JWT_SECRETS = active_jwt()
DB_URL = os.environ["HOLON_DB_URL"]
KAFKA_BOOTSTRAP = os.environ["HOLON_KAFKA_BOOTSTRAP"]
KNOWLEDGE_URL = os.environ["HOLON_KNOWLEDGE_URL"]
QDRANT_URL = os.environ["HOLON_QDRANT_URL"]
OTLP_ENDPOINT = os.environ.get("HOLON_OTLP_ENDPOINT", "")
SPICEDB_URL = os.environ["HOLON_SPICEDB_URL"]
SPICEDB_PRESHARED_KEY = os.environ["HOLON_SPICEDB_PRESHARED_KEY"]
OPA_URL = os.environ["HOLON_OPA_URL"]
S3_ENDPOINT = os.environ["HOLON_S3_ENDPOINT"]
AWS_ACCESS_KEY_ID = os.environ["AWS_ACCESS_KEY_ID"]
AWS_SECRET_ACCESS_KEY = os.environ["AWS_SECRET_ACCESS_KEY"]
AWS_REGION = os.environ["AWS_REGION"]
MODEL_BUCKET = "holon-warehouse"

INDEXER_URN = build_urn(TENANT_ID, "global", "service-account", "intelligence-indexer")
AGENT_URN = build_urn(TENANT_ID, "global", "agent", "ingest-bot")
JDOE_URN = build_urn(TENANT_ID, "global", "user", "jdoe")
WORKSPACE_URN = build_urn(TENANT_ID, "global", "workspace", WORKSPACE_ID)

pool = None
authz = None
intelligence_enabled = True
qdrant = None
llm = None
embedder = None
s3 = None

current_principal = make_principal_dependency(JWT_SECRET, secrets=JWT_SECRETS)

# SpiceDB resource_type → URN object-type segment (hyphenated).
_URN_TYPE = {
    "agent_session": "agent-session",
    "tool_plugin": "tool-plugin",
    "ml_model": "ml-model",
}


def workspace_urn(tenant_id: str, workspace_id: str | None = None) -> str:
    return build_urn(tenant_id, "global", "workspace", workspace_id or WORKSPACE_ID)


def tool_plugin_urn(tenant_id: str, name: str) -> str:
    return build_urn(tenant_id, "global", "tool-plugin", name)


def ml_model_urn(tenant_id: str, name: str) -> str:
    return build_urn(tenant_id, "global", "ml-model", name)


def agent_session_local_name(session_urn: str) -> str:
    """Last URN segment (session id hex)."""
    return session_urn.rsplit(":", 1)[-1]


def intelligence_flag_enabled() -> bool:
    return os.environ.get("HOLON_INTELLIGENCE_ENABLED", "true").lower() in {"1", "true", "yes"}


def allow_tool_plugin_register() -> bool:
    raw = (os.environ.get("HOLON_ALLOW_TOOL_PLUGIN_REGISTER") or "").strip().lower()
    if is_production():
        return raw in {"1", "true", "yes"}
    if raw == "":
        return True
    return raw in {"1", "true", "yes"}


def require_intelligence_enabled() -> None:
    if not intelligence_enabled:
        raise HolonError.unavailable(
            "PrincipalDisabled",
            "Intelligence is disabled (HOLON_INTELLIGENCE_ENABLED=false)",
        )


async def enforce_spend(principal: Principal) -> None:
    try:
        await spend_limits.enforce_before_spend(
            pool, tenant_id=principal.tenant_id, principal_urn=principal.urn
        )
    except SpendLimitExceeded as exc:
        raise HolonError.rate_limited("RateLimited", exc.detail) from exc


async def record_response_tokens(principal: Principal, body: dict) -> None:
    tokens = body.get("tokens") or {}
    total = int(tokens.get("input") or 0) + int(tokens.get("output") or 0)
    consumed = body.get("consumed") or {}
    if "tokens" in consumed:
        total = max(total, int(consumed.get("tokens") or 0))
    await spend_limits.record_tokens(pool, tenant_id=principal.tenant_id, tokens=total)


def indexer_token() -> str:
    principal = Principal(
        urn=INDEXER_URN,
        type="service_account",
        tenant_id=TENANT_ID,
        display_name="Intelligence Semantic Indexer",
    )
    return issue_token(
        principal, JWT_SECRET, ttl_seconds=300, kid=JWT_ACTIVE_KID, secrets=JWT_SECRETS
    )


def security_probe_tokens() -> tuple[str, str]:
    agent = Principal(
        urn=AGENT_URN,
        type="agent",
        tenant_id=TENANT_ID,
        display_name="Ingest Bot",
        on_behalf_of=JDOE_URN,
        country="FR",
    )
    agent_token = issue_token(agent, JWT_SECRET, ttl_seconds=60, kid=JWT_ACTIVE_KID, secrets=JWT_SECRETS)
    if is_production():
        return agent_token, agent_token
    editor = Principal(urn=JDOE_URN, type="user", tenant_id=TENANT_ID, display_name="Jane Doe", country="FR")
    return (
        agent_token,
        issue_token(editor, JWT_SECRET, ttl_seconds=60, kid=JWT_ACTIVE_KID, secrets=JWT_SECRETS, allow_user=True),
    )


def require_own_session(session: dict | None, principal: Principal) -> dict:
    if session is None or session["agent_urn"] != principal.urn:
        raise HolonError.not_found("AgentSessionNotFound", "no agent_session found for that urn")
    return session


def tool_plugin_not_found(name: str) -> HolonError:
    return HolonError.not_found(
        "ToolPluginNotFound", f"no agent tool plugin registered as {name!r}", name=name
    )


def model_not_found(name: str) -> HolonError:
    return HolonError.not_found("ModelNotFound", f"no model registered as {name!r}", name=name)


async def _authorize_workspace(
    principal: Principal, permission: str, *, workspace_id: Optional[str] = None
) -> None:
    urn = workspace_urn(principal.tenant_id, workspace_id)
    decision = await authz.authorize(
        principal, resource_type="workspace", resource_urn=urn, permission=permission
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


async def _authorize_agent_session(
    principal: Principal, permission: str, *, session_urn: str
) -> None:
    decision = await authz.authorize(
        principal,
        resource_type="agent_session",
        resource_urn=session_urn,
        permission=permission,
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


async def _authorize_tool_plugin(
    principal: Principal, permission: str, *, name: str
) -> None:
    urn = tool_plugin_urn(principal.tenant_id, name)
    decision = await authz.authorize(
        principal, resource_type="tool_plugin", resource_urn=urn, permission=permission
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


async def _authorize_ml_model(
    principal: Principal, permission: str, *, name: str
) -> None:
    urn = ml_model_urn(principal.tenant_id, name)
    decision = await authz.authorize(
        principal, resource_type="ml_model", resource_urn=urn, permission=permission
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


async def _seed_agent_session_authz(
    *, tenant_id: str, session_urn: str, compensate_delete
) -> str:
    from .authz_seed import seed_agent_session_parent_workspace

    try:
        return await seed_agent_session_parent_workspace(
            authz, tenant_id=tenant_id, session_urn=session_urn
        )
    except Exception as exc:
        if compensate_delete is not None:
            await compensate_delete()
        raise HolonError.unavailable(
            "AuthzSeedFailed",
            f"failed to seed SpiceDB relationship for agent_session {session_urn!r}: {exc}",
            session_urn=session_urn,
        ) from exc


async def _seed_tool_plugin_authz(
    *, tenant_id: str, name: str, compensate_delete
) -> str:
    from .authz_seed import seed_tool_plugin_parent_workspace

    try:
        return await seed_tool_plugin_parent_workspace(
            authz, tenant_id=tenant_id, name=name
        )
    except Exception as exc:
        if compensate_delete is not None:
            await compensate_delete()
        raise HolonError.unavailable(
            "AuthzSeedFailed",
            f"failed to seed SpiceDB relationship for tool_plugin {name!r}: {exc}",
            name=name,
        ) from exc


async def _seed_ml_model_authz(
    *, tenant_id: str, name: str, compensate_delete
) -> str:
    from .authz_seed import seed_ml_model_parent_workspace

    try:
        return await seed_ml_model_parent_workspace(authz, tenant_id=tenant_id, name=name)
    except Exception as exc:
        if compensate_delete is not None:
            await compensate_delete()
        raise HolonError.unavailable(
            "AuthzSeedFailed",
            f"failed to seed SpiceDB relationship for ml_model {name!r}: {exc}",
            name=name,
        ) from exc


async def _unlink_resource_authz(
    resource_type: str, *, tenant_id: str, name: str
) -> None:
    """Drop parent_workspace after the Postgres row is gone."""
    urn_type = _URN_TYPE.get(resource_type, resource_type)
    urn = build_urn(tenant_id, "global", urn_type, name)
    try:
        await authz.delete_relationship(
            resource_type=resource_type,
            resource_urn=urn,
            relation="parent_workspace",
            subject_type="workspace",
            subject_urn=workspace_urn(tenant_id),
        )
    except Exception:
        logger.exception(
            "SpiceDB parent_workspace cleanup failed for deleted %s %s", resource_type, urn
        )


async def _unlink_agent_session_authz(*, tenant_id: str, session_urn: str) -> None:
    try:
        await authz.delete_relationship(
            resource_type="agent_session",
            resource_urn=session_urn,
            relation="parent_workspace",
            subject_type="workspace",
            subject_urn=workspace_urn(tenant_id),
        )
    except Exception:
        logger.exception(
            "SpiceDB parent_workspace cleanup failed for deleted agent_session %s", session_urn
        )


async def _filter_readable(
    principal: Principal, resource_type: str, rows: list[dict], *, urn_fn
) -> list[dict]:
    """Keep rows the principal (and its mandant, if delegated) can `read`."""
    if not rows:
        return []
    try:
        readable = await authz.lookup_resource_ids(
            resource_type=resource_type, permission="read", principal_urn=principal.urn
        )
        if principal.on_behalf_of:
            readable &= await authz.lookup_resource_ids(
                resource_type=resource_type, permission="read", principal_urn=principal.on_behalf_of
            )
        return [row for row in rows if spicedb_object_id(urn_fn(row)) in readable]
    except Exception:
        logger.exception(
            "%s LookupResources failed; falling back to per-row CheckPermission", resource_type
        )
        allowed = []
        for row in rows:
            urn = urn_fn(row)
            if not await authz.check_rebac(principal.urn, resource_type, urn, "read"):
                continue
            if principal.on_behalf_of and not await authz.check_rebac(
                principal.on_behalf_of, resource_type, urn, "read"
            ):
                continue
            allowed.append(row)
        return allowed
