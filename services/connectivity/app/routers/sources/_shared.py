"""Shared source-route helpers (authz upsert, status, delete+audit)."""
from __future__ import annotations

from typing import Optional

from holon_common import HolonError, Principal
from holon_common.audit import emit_audit

from ... import deps
from ...deps import (
    _authorize_source,
    _unlink_resource_authz,
    resource_workspace,
    source_urn,
)
from ...ingest import _source_not_found


async def _authorize_source_update(
    registry, principal: Principal, name: str, target_workspace: str
) -> Optional[dict]:
    """Re-registration is an upsert: require write on the existing source and
    keep it in its workspace (a move would leave a stale parent_workspace)."""
    existing = await registry.get_source(deps.pool, principal.tenant_id, name)
    if existing is None:
        return None
    stored = resource_workspace(existing)
    await _authorize_source(principal, "write", name=name, workspace_id=stored)
    if stored != target_workspace:
        raise HolonError.invalid_argument(
            "SourceWorkspaceMismatch",
            f"source {name!r} is bound to workspace {stored!r}; got {target_workspace!r}",
            name=name,
            expected_workspace_id=stored,
            got_workspace_id=target_workspace,
        )
    return existing


async def _set_source_status(registry, principal: Principal, name: str, status: str) -> dict:
    source = await registry.get_source(deps.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    await _authorize_source(principal, "write", name=name, workspace_id=resource_workspace(source))
    return await registry.set_source_status(deps.pool, principal.tenant_id, name, status)


async def _delete_registered_source(
    registry, principal: Principal, name: str, *, audit_action: str
) -> dict:
    source = await registry.get_source(deps.pool, principal.tenant_id, name)
    if source is None:
        raise _source_not_found(name)
    ws = resource_workspace(source)
    await _authorize_source(principal, "write", name=name, workspace_id=ws)
    await registry.delete_source(deps.pool, principal.tenant_id, name)
    await _unlink_resource_authz("source", tenant_id=principal.tenant_id, workspace_id=ws, name=name)
    emit_audit(
        category="access",
        action=audit_action,
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="source",
        resource_urn=source_urn(principal.tenant_id, ws, name),
    )
    return {"deleted": name}
