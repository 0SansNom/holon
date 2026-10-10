"""Identity grant / revoke / outbox fan-out for ReBAC changes."""
from __future__ import annotations

import uuid

from holon_common import EventActor, EventEnvelope, HolonError, Principal, outbox
from holon_common.audit import emit_audit
from holon_common.correlation import current_correlation_id

from . import deps
from .governance import _access_listing


def _grant_subject_relation(target: Principal) -> str | None:
    """Map principal to SpiceDB userset (group#member vs principal directly)."""
    return "member" if target.type == "group" else None

async def _require_grant_target(urn: str, *, tenant_id: str) -> Principal:
    target = await deps._fetch_principal(deps.pool, urn)
    if target is None:
        raise HolonError.not_found('PrincipalNotFound', f"unknown principal: {urn}")
    if target.tenant_id != tenant_id:
        raise HolonError.invalid_argument('CrossTenantPrincipal', "principal belongs to another tenant")
    return target

async def _require_group(group_urn: str) -> Principal:
    group = await deps._fetch_principal(deps.pool, group_urn)
    if group is None:
        raise HolonError.not_found("PrincipalNotFound", f"unknown principal: {group_urn}")
    if group.type != "group":
        raise HolonError.invalid_argument("NotAGroup", f"{group_urn} is not a group", urn=group_urn)
    return group

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
    wid = workspace_id or deps.WORKSPACE_ID
    event = EventEnvelope(
        event_id=event_id,
        event_type=event_type,
        tenant_id=tid,
        workspace_id=wid,
        aggregate_type="Principal",
        aggregate_id=target_principal_urn,
        correlation_id=current_correlation_id() or event_id,
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
    async with deps.pool.acquire() as conn:
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
        deps.pool,
        target_principal_urn=target_principal_urn,
        status=status,
        actor=actor,
        tenant_id=tenant_id,
        workspace_id=deps.WORKSPACE_ID,
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

async def _apply_access_change(
    *,
    granted: bool,
    target: Principal,
    resource_type: str,
    resource_urn: str,
    relation: str,
    actor: Principal,
    tenant_id: str,
    workspace_id: str | None = None,
) -> None:
    """Write or delete a direct grant, then enqueue, fan out groups, and audit.

    Workspace and project routes share this tail. Callers authorize and
    validate the relation first. Group membership stays on its own path.
    """
    event_type = "identity.permission.granted" if granted else "identity.permission.revoked"
    change = deps.authz.write_relationship if granted else deps.authz.delete_relationship
    await change(
        resource_type=resource_type,
        resource_urn=resource_urn,
        relation=relation,
        subject_urn=target.urn,
        optional_subject_relation=_grant_subject_relation(target),
    )
    await _enqueue_permission_event(
        event_type=event_type,
        target_principal_urn=target.urn,
        resource_type=resource_type,
        resource_urn=resource_urn,
        relation=relation,
        actor=actor,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if target.type == "group":
        await _fanout_group_permission_event(
            target,
            event_type=event_type,
            resource_type=resource_type,
            resource_urn=resource_urn,
            relation=relation,
            actor=actor,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
    verb = "granted" if granted else "revoked"
    preposition = "to" if granted else "from"
    emit_audit(
        category="identity",
        action=event_type,
        outcome="success",
        tenant_id=tenant_id,
        actor_urn=actor.urn,
        actor_type=actor.type,
        resource_type=resource_type,
        resource_urn=resource_urn,
        permission=relation,
        reason=f"{verb} {relation} {preposition} {target.urn}",
        extra={"targetPrincipalUrn": target.urn},
    )