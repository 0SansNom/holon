"""ObjectType definition list/get plus ontology health-check."""

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


@router.get("/ontology")
async def list_ontology_definitions(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """A real, previously-missing gap: every other governed resource type
    (`RelationType`, `Action`) already had a list endpoint; `ObjectType`
    never did — every existing caller already knew the six hardcoded
    names. Same auth-only convention as `/relation-types`/`/actions`.
    """
    return await ontology.list_object_types(core.pool, principal.tenant_id)


@router.get("/ontology/health-check")
async def get_ontology_health_check(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """Structural anti-pattern detection (`ontology_health.py`) — registered
    *before* `/ontology/{name}` below, or that path-param route would
    swallow the literal `health-check` segment as an ObjectType name (the
    same route-ordering discipline `routers/objects/object_reads.py`'s module
    docstring already documents for its own literal-vs-templated routes).
    Same auth-only tier as `/ontology` — aggregated metadata and null-rate
    percentages only, never raw instance values.
    """
    return await ontology_health.run_health_check(principal)


@router.get("/ontology/{name}")
async def get_ontology_definition(name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    """Inspects an ObjectType *definition* — property mapping, computed
    classification — as opposed to `/objects/{name}` which resolves its
    *instances*. Metadata, not data: gated by authentication only, like
    `/catalog/datasets`, not by the PDP (row/column security has
    nothing to enforce on a definition with no rows).
    """
    object_type_urn = ontology.object_type_urn(principal.tenant_id, workspace_id, name)
    object_type = await ontology.get_object_type(core.pool, object_type_urn)
    if object_type is None:
        raise HolonError.not_found('ObjectTypeNotFound', f"unknown ObjectType: {name}")
    return object_type
