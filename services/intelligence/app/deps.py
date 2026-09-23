"""Intelligence shared helpers and process-level runtime.

`pool` / `authz` are set by `main.py`'s lifespan.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from holon_common import HolonError, Principal, build_urn
from holon_common.spicedb_id import spicedb_object_id

logger = logging.getLogger("intelligence.authz")

TENANT_ID = os.environ["HOLON_TENANT_ID"]
WORKSPACE_ID = os.environ["HOLON_WORKSPACE_ID"]

pool = None
authz = None

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
