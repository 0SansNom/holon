"""ObjectType definition list/get plus ontology health-check."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from holon_common import HolonError, Principal

from ... import ontology, ontology_health
from ... import core

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
