"""Shared authz helpers for the ontology governance surface."""

from __future__ import annotations

import logging
import os
from typing import Optional

from holon_common import HolonError, Principal, build_urn, issue_token

from ... import core, ontology

logger = logging.getLogger("knowledge.ontology_admin")

IDENTITY_URL = os.environ["HOLON_IDENTITY_URL"]
IDENTITY_VALIDATOR_URN = build_urn(core.TENANT_ID, "global", "service-account", "knowledge-project-validator")

_ALLOWED_CLASSIFICATIONS = {"public", "internal", "confidential", "restricted"}


def _identity_validation_token() -> str:
    """Mints a short-lived service-account token directly (same trust
    level already extended to every service holding `HOLON_JWT_SECRET`,
    e.g. Intelligence's `_indexer_token`) rather than round-tripping
    through Identity's `/token` — this is an internal existence-check
    call (`GET /projects/{name}`), not a client-facing sign-in.
    """
    principal = Principal(
        urn=IDENTITY_VALIDATOR_URN,
        type="service_account",
        tenant_id=core.TENANT_ID,
        display_name="Knowledge Project Validator",
    )
    return issue_token(
        principal, core.JWT_SECRET, ttl_seconds=60, kid=core.JWT_ACTIVE_KID, secrets=core.JWT_SECRETS
    )

async def _authorize_ontology_governance(principal: Principal, workspace_id: str) -> None:
    """Ontology lifecycle changes (versioning/publication) are a
    governance action, same tier as `create_relation_type` — the
    workspace's own `approve` permission (admin-only), not
    `_authorize_object_type` (there's no read/write of instance data
    happening here). `workspace_id` comes from `core.current_workspace` —
    the caller-specified workspace, never the bootstrap constant, so this
    check means something for a non-bootstrap tenant too.
    """
    decision = await core.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=ontology.workspace_urn(principal.tenant_id, workspace_id),
        permission="approve",
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


async def _authorize_workspace_read(principal: Principal, workspace_id: str) -> None:
    decision = await core.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=ontology.workspace_urn(principal.tenant_id, workspace_id),
        permission="read",
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


async def _authorize_marking_administer(
    principal: Principal, workspace_id: str, marking_name: str
) -> None:
    """Grant/revoke clearance: marking `administer` **or** workspace
    `approve` (bootstrap / ontology admins who did not create the marking).
    """
    marking_urn = build_urn(principal.tenant_id, "global", "marking", marking_name)
    if await core.authz.check_rebac(principal.urn, "marking", marking_urn, "administer"):
        return
    await _authorize_ontology_governance(principal, workspace_id)


async def _authorize_ontology_write(principal: Principal, workspace_id: str) -> None:
    """Branch creation is a lighter-weight governance action than
    publishing — workspace `write` (editor+), not `approve` (admin-only).
    This is what makes review meaningful: the same role separation
    Actions already rely on for `action_approval` (an editor can request,
    only an admin can decide), not a same-URN check.
    """
    decision = await core.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=ontology.workspace_urn(principal.tenant_id, workspace_id),
        permission="write",
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


async def _authorize_shared_property_type(principal: Principal, urn: str, permission: str) -> None:
    """Per-URN ReBAC for Shared Property Types (parent_workspace cascade)."""
    decision = await core.authz.authorize(
        principal,
        resource_type="shared_property_type",
        resource_urn=urn,
        permission=permission,
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


async def _authorize_relation_type(principal: Principal, urn: str, permission: str) -> None:
    """Per-URN ReBAC for RelationTypes / Foundry Link Types."""
    decision = await core.authz.authorize(
        principal,
        resource_type="relation_type",
        resource_urn=urn,
        permission=permission,
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


async def _authorize_value_type(principal: Principal, urn: str, permission: str) -> None:
    """Per-URN ReBAC for Value Types (parent_workspace + optional project)."""
    decision = await core.authz.authorize(
        principal,
        resource_type="value_type",
        resource_urn=urn,
        permission=permission,
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


async def _seed_shared_property_type_authz(
    *, tenant_id: str, workspace_id: str, urn: str, api_name: str
) -> None:
    """Write parent_workspace; compensate by deleting the SPT row on failure."""
    try:
        await core.authz.write_relationship(
            resource_type="shared_property_type",
            resource_urn=urn,
            relation="parent_workspace",
            subject_type="workspace",
            subject_urn=ontology.workspace_urn(tenant_id, workspace_id),
        )
    except Exception as exc:
        logger.exception("SpiceDB parent_workspace write failed for SPT %s — compensating PG delete", urn)
        try:
            await ontology.delete_shared_property_type(core.pool, tenant_id=tenant_id, api_name=api_name)
        except Exception:
            logger.exception(
                "compensating SPT delete also failed for %s — row may exist in PG without ReBAC parent",
                urn,
            )
        raise HolonError.unavailable('Unavailable', f"failed to seed shared_property_type authz relationship: {exc}",) from exc


async def _seed_relation_type_authz(*, tenant_id: str, workspace_id: str, urn: str, name: str) -> None:
    try:
        await core.authz.write_relationship(
            resource_type="relation_type",
            resource_urn=urn,
            relation="parent_workspace",
            subject_type="workspace",
            subject_urn=ontology.workspace_urn(tenant_id, workspace_id),
        )
    except Exception as exc:
        logger.exception("SpiceDB parent_workspace write failed for RelationType %s — compensating PG delete", urn)
        try:
            await core.pool.execute("DELETE FROM relation_type WHERE urn = $1", urn)
        except Exception:
            logger.exception(
                "compensating RelationType delete also failed for %s — row may exist in PG without ReBAC parent",
                urn,
            )
        raise HolonError.unavailable('Unavailable', f"failed to seed relation_type authz relationship: {exc}",) from exc


async def _seed_value_type_authz(*, tenant_id: str, workspace_id: str, urn: str, name: str) -> None:
    try:
        await core.authz.write_relationship(
            resource_type="value_type",
            resource_urn=urn,
            relation="parent_workspace",
            subject_type="workspace",
            subject_urn=ontology.workspace_urn(tenant_id, workspace_id),
        )
    except Exception as exc:
        logger.exception("SpiceDB parent_workspace write failed for ValueType %s — compensating PG delete", urn)
        try:
            await ontology.delete_value_type(core.pool, tenant_id=tenant_id, name=name)
        except Exception:
            logger.exception(
                "compensating ValueType delete also failed for %s — row may exist in PG without ReBAC parent",
                urn,
            )
        raise HolonError.unavailable('Unavailable', f"failed to seed value_type authz relationship: {exc}",) from exc


async def _link_relation_type_to_project(relation_urn: str, project_urn: Optional[str]) -> None:
    await _link_resource_to_project(
        resource_type="relation_type", resource_urn=relation_urn, project_urn=project_urn
    )


async def _link_resource_to_project(
    *, resource_type: str, resource_urn: str, project_urn: Optional[str]
) -> None:
    """Reconcile SpiceDB `parent_project` for a single-valued Postgres
    `project_urn` (ObjectType publish path and Shared Property Type CRUD).

    SpiceDB relationships are additive (`OPERATION_TOUCH`), so changing
    or clearing project scope would leave stale edges unless we delete
    the previous subjects. Order is write-new-then-delete-old.
    """
    existing = await core.authz.read_relationships(
        resource_type=resource_type, resource_urn=resource_urn, relation="parent_project"
    )
    existing_urns = [relationship["subject"]["object"]["objectId"] for relationship in existing]

    async def _write(subject_urn: str) -> None:
        await core.authz.write_relationship(
            resource_type=resource_type,
            resource_urn=resource_urn,
            relation="parent_project",
            subject_type="project",
            subject_urn=subject_urn,
        )

    async def _delete(subject_urn: str) -> None:
        await core.authz.delete_relationship(
            resource_type=resource_type,
            resource_urn=resource_urn,
            relation="parent_project",
            subject_type="project",
            subject_urn=subject_urn,
        )

    async def _restore_snapshot() -> None:
        try:
            current = await core.authz.read_relationships(
                resource_type=resource_type, resource_urn=resource_urn, relation="parent_project"
            )
            current_urns = {relationship["subject"]["object"]["objectId"] for relationship in current}
            for urn in existing_urns:
                if urn not in current_urns:
                    await _write(urn)
            if project_urn is not None and project_urn not in existing_urns and project_urn in current_urns:
                await _delete(project_urn)
        except Exception:
            logger.exception(
                "failed to restore parent_project snapshot for %s after link error", resource_urn
            )

    try:
        if project_urn is not None:
            if project_urn not in existing_urns:
                await _write(project_urn)
            for old_urn in existing_urns:
                if old_urn != project_urn:
                    await _delete(old_urn)
        else:
            for old_urn in existing_urns:
                await _delete(old_urn)
    except Exception:
        logger.exception("SpiceDB parent_project reconcile failed for %s — attempting restore", resource_urn)
        await _restore_snapshot()
        raise


async def _link_object_type_to_project(object_type_urn: str, project_urn: Optional[str]) -> None:
    """ObjectType publish/merge → SpiceDB parent_project reconcile."""
    await _link_resource_to_project(
        resource_type="object_type", resource_urn=object_type_urn, project_urn=project_urn
    )


async def _link_shared_property_type_to_project(spt_urn: str, project_urn: Optional[str]) -> None:
    """SPT create/update → SpiceDB parent_project reconcile."""
    await _link_resource_to_project(
        resource_type="shared_property_type", resource_urn=spt_urn, project_urn=project_urn
    )


async def _link_value_type_to_project(value_type_resource_urn: str, project_urn: Optional[str]) -> None:
    """Value Type create/update → SpiceDB parent_project (project import)."""
    await _link_resource_to_project(
        resource_type="value_type", resource_urn=value_type_resource_urn, project_urn=project_urn
    )


async def _validate_optional_project_urn(project_urn: Optional[str]) -> Optional[str]:
    if project_urn is None:
        return None
    cleaned = project_urn.strip() if isinstance(project_urn, str) else ""
    if not cleaned:
        return None
    from ...ontology.publishing import _validate_project_scope

    try:
        await _validate_project_scope(
            identity_url=IDENTITY_URL,
            project_urn=cleaned,
            identity_token=_identity_validation_token(),
        )
    except ValueError as exc:
        raise HolonError.invalid_argument("InvalidProjectScope", str(exc)) from exc
    return cleaned


def _validate_resource_type(resource_type: str) -> None:
    if resource_type not in ontology.ALLOWED_RESOURCE_TYPES:
        raise HolonError.invalid_argument('InvalidResourceType', f"unknown resource_type: {resource_type!r} (expected one of {sorted(ontology.ALLOWED_RESOURCE_TYPES)})",
        )

