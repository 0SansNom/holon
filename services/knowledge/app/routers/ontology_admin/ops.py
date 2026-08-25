"""Query log, audit trail, glossary, and action catalog."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from pyiceberg.exceptions import NoSuchTableError

from holon_common import EventActor, EventEnvelope, HolonError, Principal, build_urn

from ... import actions, catalog, glossary, ontology, ontology_health, query_log, resolver
from ... import core
from ...paging import interface_instance_key
from ..objects.paging_deps import page_response, paging_query
from ._auth import (
    IDENTITY_URL,
    _ALLOWED_CLASSIFICATIONS,
    _authorize_marking_administer,
    _authorize_ontology_governance,
    _authorize_ontology_write,
    _authorize_relation_type,
    _authorize_shared_property_type,
    _authorize_value_type,
    _authorize_workspace_read,
    _identity_validation_token,
    _link_object_type_to_project,
    _link_relation_type_to_project,
    _link_shared_property_type_to_project,
    _link_value_type_to_project,
    _seed_relation_type_authz,
    _seed_shared_property_type_authz,
    _seed_value_type_authz,
    _validate_optional_project_urn,
    _validate_resource_type,
)

router = APIRouter()
logger = logging.getLogger("knowledge.ontology_admin")


@router.get("/query-log")
async def get_query_log(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """Read surface for the anonymized query log, so it's genuinely
    inspectable rather than write-only. Auth-only, tenant-scoped.
    """
    return await query_log.list_recent(core.pool, principal.tenant_id)


@router.get("/audit-events")
async def list_audit_events(
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
    category: Optional[str] = None,
    action: Optional[str] = None,
    actor: Optional[str] = None,
    outcome: Optional[str] = None,
    pageSize: Optional[int] = None,
    pageToken: Optional[str] = None,
) -> dict:
    """Queryable security audit trail (durable). Requires workspace approve.

    Events are available as soon as they are written. SIEM operators still
    ship ``holon.audit`` stdout independently.
    """
    from holon_common import audit_store as audit_store_module
    from holon_common.audit import CATEGORIES
    from ...paging import PagingError, clamp_page_size, decode_cursor, encode_cursor

    await _authorize_ontology_governance(principal, workspace_id)
    if category is not None and category not in CATEGORIES:
        raise HolonError.invalid_argument("InvalidAuditCategory", f"unknown category: {category}", category=category)
    try:
        page_size = clamp_page_size(pageSize)
    except PagingError as exc:
        raise HolonError.invalid_argument("InvalidPageSize", str(exc)) from exc
    after_id = None
    if pageToken:
        try:
            after_id = int(decode_cursor(pageToken))
        except (PagingError, TypeError, ValueError) as exc:
            raise HolonError.invalid_argument("InvalidPageToken", "invalid pageToken") from exc

    rows = await audit_store_module.list_events(
        core.pool,
        principal.tenant_id,
        category=category,
        action=action,
        actor_urn=actor,
        outcome=outcome,
        after_id=after_id,
        page_size=page_size + 1,
    )
    next_token = None
    if len(rows) > page_size:
        rows = rows[:page_size]
        next_token = encode_cursor(after_id=rows[-1]["id"])
    return {"data": rows, "nextPageToken": next_token, "pageSize": page_size}


@router.get("/glossary")
async def list_glossary(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """Populated business glossary endpoint. Auth-only,
    same convention as `/ontology/{name}` — metadata, not instance data.
    """
    return await glossary.list_terms(core.pool, principal.tenant_id)


@router.get("/glossary/{term}")
async def get_glossary_term(term: str, principal: Principal = Depends(core.current_principal)) -> dict:
    result = await glossary.get_term(core.pool, principal.tenant_id, term)
    if result is None:
        raise HolonError.not_found('GlossaryTermNotFound', f"unknown glossary term: {term!r}")
    return result


class GlossaryTermRequest(BaseModel):
    term: str
    definition: str
    synonyms: list[str] = []
    related_object_type: Optional[str] = None


@router.post("/glossary", status_code=201)
async def create_glossary_term(
    request: GlossaryTermRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """Registering a glossary term is ontology governance, same tier and
    gate as RelationTypes/ActionTypes — not a per-ObjectType concern
    (`related_object_type` is optional context, not an authorization
    scope), so `approve` on the workspace is what's checked.
    """
    decision = await core.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=ontology.workspace_urn(principal.tenant_id, workspace_id),
        permission="approve",
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)

    if await glossary.get_term(core.pool, principal.tenant_id, request.term) is not None:
        raise HolonError.conflict('GlossaryTermAlreadyExists', f"glossary term already exists: {request.term}")

    related_urn = None
    if request.related_object_type is not None:
        related_urn = ontology.object_type_urn(principal.tenant_id, workspace_id, request.related_object_type)
        if await ontology.get_object_type(core.pool, related_urn) is None:
            raise HolonError.not_found('ObjectTypeNotFound', f"unknown ObjectType: {request.related_object_type}")

    return await glossary.create_term(
        core.pool,
        tenant_id=principal.tenant_id,
        term=request.term,
        definition=request.definition,
        synonyms=request.synonyms,
        related_object_type_urn=related_urn,
    )


@router.get("/actions")
async def list_actions(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """Read surface for every registered Action Type — auth-only,
    same convention as `/ontology/{name}`/`/relation-types` (metadata about a
    definition, not an instance read). Exists so mandatory descriptions are
    actually queryable (including by an agent tool-compiler).
    """
    return [
        {
            "name": action_type["name"],
            "target_object_type": action_type["target_object_type"],
            "target_interface": action_type.get("target_interface"),
            "required_permission": action_type["required_permission"],
            "risk_level": action_type["risk_level"],
            "description": action_type["description"],
            "function_side_effect": action_type.get("function_side_effect"),
            "writeback_dataset": action_type.get("writeback_dataset"),
            "parameters": action_type["parameters"],
            "edits": action_type["edits"],
            "edit_function": action_type.get("edit_function"),
            "sections": action_type.get("sections", []),
            "type_classes": action_type.get("type_classes", []),
        }
        for action_type in await ontology.list_action_types(core.pool, principal.tenant_id)
    ]


@router.get("/actions/{name}")
async def get_action(name: str, principal: Principal = Depends(core.current_principal)) -> dict:
    definition = await actions._get_action_definition(core.pool, principal.tenant_id, name)
    if definition is None:
        raise HolonError.not_found('ActionNotFound', f"unknown Action: {name}")
    public = {k: v for k, v in definition.items() if k != "_declarative"}
    return {"name": name, **public}
