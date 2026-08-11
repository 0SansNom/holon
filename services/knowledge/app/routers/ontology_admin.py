"""Ontology governance surface — ObjectType CRUD/versions/branches,
interfaces, markings, relation-types, plus the read-only glossary/
query-log/actions catalogs that share the same "metadata, auth-only"
shape. Everything here is a governance action or a definition read, not
an instance read — the one instance-shaped exception,
`POST /objects/{type}/{id}/markings`, is grouped here rather than in
`routers/objects.py` because it's a markings/governance write, not an
object read.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from pyiceberg.exceptions import NoSuchTableError

from holon_common import Principal, build_urn, issue_token

from .. import actions, catalog, glossary, ontology, ontology_health, query_log, resolver
from .. import core

router = APIRouter()
logger = logging.getLogger("knowledge.ontology_admin")

IDENTITY_URL = os.environ["HOLON_IDENTITY_URL"]
IDENTITY_VALIDATOR_URN = build_urn(core.TENANT_ID, "global", "service-account", "knowledge-project-validator")


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


@router.get("/catalog/datasets")
async def get_datasets(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    return await catalog.list_datasets(core.pool, principal.tenant_id)


@router.get("/catalog/datasets/{name}/preview")
async def preview_dataset(name: str, principal: Principal = Depends(core.current_principal)) -> dict:
    """One sample row's column names — what a "Create Object Type" form
    suggests a property mapping from, instead of asking a non-technical
    admin to type raw JSON keys from memory. Auth-only, like every other
    definition/metadata read in this router: a column *name* isn't row
    data the PDP has anything to enforce on, the sample value shown is
    illustrative only (never asserted to be safe to persist anywhere).
    """
    try:
        rows = await asyncio.to_thread(resolver.fetch_generic, name, **core.ICEBERG_CONFIG)
    except NoSuchTableError:
        raise HTTPException(status_code=404, detail=f"dataset {name!r} has never been synced")
    if not rows:
        return {"columns": []}
    return {"columns": [{"name": key, "sample": value} for key, value in rows[0].items()]}


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
    same route-ordering discipline `routers/objects/seeded.py`'s module
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
    object_type_urn = core.OBJECT_TYPE_URNS.get(name) or ontology.object_type_urn(principal.tenant_id, workspace_id, name)
    object_type = await ontology.get_object_type(core.pool, object_type_urn)
    if object_type is None:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {name}")
    return object_type


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
        raise HTTPException(status_code=403, detail=decision.reason)


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
        raise HTTPException(status_code=403, detail=decision.reason)


_ALLOWED_CLASSIFICATIONS = {"public", "internal", "confidential", "restricted"}


class CreateObjectTypeRequest(BaseModel):
    name: str
    source_dataset_urn: str
    property_mapping: dict[str, str]
    description: str = ""
    column_classification: dict[str, str] = {}
    primary_key: str = "id"
    title_key: Optional[str] = None
    plural_display_name: str = ""
    lifecycle_status: str = "experimental"
    visibility: str = "normal"
    icon: Optional[str] = None


@router.post("/object-types", status_code=201)
async def create_object_type(request: CreateObjectTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    """The self-serve half of the no-code connector: an already-synced
    Dataset (see `connectivity`'s `/sources`) becomes a real, browsable
    ObjectType — same governance tier as publishing a version
    (`_authorize_ontology_governance`, admin-only `approve`) since this
    takes effect immediately, no draft/publish step, the same way the six
    boot-seeded types are immediately live.

    Creation itself is existence + mapping only; branching, interfaces,
    markings, and the rest of the lifecycle attach afterward via
    `POST /ontology/{name}/versions` (+ publish) / branches — same path
    a seeded type uses.
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if not request.property_mapping:
        raise HTTPException(status_code=400, detail="property_mapping must name at least one property")
    bad_values = set(request.column_classification.values()) - _ALLOWED_CLASSIFICATIONS
    if bad_values:
        raise HTTPException(
            status_code=400,
            detail=f"unknown classification value(s) {sorted(bad_values)} (expected one of {sorted(_ALLOWED_CLASSIFICATIONS)})",
        )
    try:
        object_type = await ontology.create_object_type(
            core.pool,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            name=request.name,
            source_dataset_urn=request.source_dataset_urn,
            property_mapping=request.property_mapping,
            description=request.description,
            column_classification=request.column_classification,
            primary_key=request.primary_key,
            title_key=request.title_key,
            plural_display_name=request.plural_display_name,
            lifecycle_status=request.lifecycle_status,
            visibility=request.visibility,
            icon=request.icon,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409 if "already exists" in str(exc) else 400, detail=str(exc)) from exc

    # Without this, `_authorize_object_type` denies everyone, including
    # the creator — `object_type.read/write/approve` all reduce to
    # `parent_workspace->...`, the same relationship `authz_seed.py`
    # writes for the six boot-seeded types at startup. PG and SpiceDB
    # are not in one transaction: on authz failure, compensate by
    # deleting the Postgres row so we never leave an unreadable orphan.
    try:
        await core.authz.write_relationship(
            resource_type="object_type",
            resource_urn=object_type["urn"],
            relation="parent_workspace",
            subject_type="workspace",
            subject_urn=ontology.workspace_urn(principal.tenant_id, workspace_id),
        )
    except Exception as exc:
        logger.exception("SpiceDB parent_workspace write failed for %s — compensating PG delete", object_type["urn"])
        try:
            await ontology.delete_object_type(core.pool, object_type["urn"])
        except Exception:
            logger.exception(
                "compensating delete also failed for %s — ObjectType may exist in PG without ReBAC parent",
                object_type["urn"],
            )
        raise HTTPException(
            status_code=503,
            detail=f"failed to seed object_type authz relationship: {exc}",
        ) from exc
    return object_type


class ProposeObjectTypeVersionRequest(BaseModel):
    property_mapping: Optional[dict] = None
    description: Optional[str] = None
    implements: Optional[list[str]] = None
    derived_properties: Optional[dict[str, str | dict]] = None
    project_urn: Optional[str] = None
    markings: Optional[list[str]] = None
    property_formats: Optional[dict[str, dict]] = None
    conditional_formats: Optional[dict[str, list]] = None
    property_types: Optional[dict[str, dict]] = None
    primary_key: Optional[str] = None
    title_key: Optional[str] = None
    plural_display_name: Optional[str] = None
    lifecycle_status: Optional[str] = None
    visibility: Optional[str] = None
    icon: Optional[str] = None


@router.post("/ontology/{name}/versions", status_code=201)
async def propose_object_type_version(
    name: str, request: ProposeObjectTypeVersionRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """Ontology lifecycle versioning. Creates a `draft` — never
    touches the live definition every other read path uses until
    `POST /ontology/{name}/versions/{version}/publish` says otherwise.
    Workspace `write` (editor+), same tier as branch creation — publishing
    and review stay `approve` (admin-only).
    """
    await _authorize_ontology_write(principal, workspace_id)
    try:
        object_type_urn = await core._object_type_urn_for(name, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {name}")
    try:
        return await ontology.propose_object_type_version(
            core.pool,
            object_type_urn=object_type_urn,
            property_mapping=request.property_mapping,
            description=request.description,
            implements=request.implements,
            derived_properties=request.derived_properties,
            project_urn=request.project_urn,
            markings=request.markings,
            property_formats=request.property_formats,
            conditional_formats=request.conditional_formats,
            property_types=request.property_types,
            primary_key=request.primary_key,
            title_key=request.title_key,
            plural_display_name=request.plural_display_name,
            lifecycle_status=request.lifecycle_status,
            visibility=request.visibility,
            icon=request.icon,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ValueTypeRequest(BaseModel):
    name: str
    base_type: str
    format_regex: Optional[str] = None
    constraints: Optional[list[dict]] = None
    description: str = ""


@router.post("/value-types", status_code=201)
async def create_value_type(request: ValueTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    """Registering a Value Type is ontology governance, same tier as
    registering an Interface or a Marking — the workspace's own
    `approve` permission. A separate registry from `property_formats`
    (display-only) — this one is real data typing, referenced by
    `property_types` (below) and by declarative Action parameters.
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_value_type(core.pool, principal.tenant_id, request.name) is not None:
        raise HTTPException(status_code=409, detail=f"value type already exists: {request.name}")
    try:
        return await ontology.create_value_type(
            core.pool,
            tenant_id=principal.tenant_id,
            name=request.name,
            base_type=request.base_type,
            format_regex=request.format_regex,
            constraints=request.constraints,
            description=request.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/value-types")
async def list_value_types(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    return await ontology.list_value_types(core.pool, principal.tenant_id)


@router.get("/value-types/{name}")
async def get_value_type(name: str, principal: Principal = Depends(core.current_principal)) -> dict:
    value_type = await ontology.get_value_type(core.pool, principal.tenant_id, name)
    if value_type is None:
        raise HTTPException(status_code=404, detail=f"unknown value type: {name}")
    return value_type


class UpdateValueTypeRequest(BaseModel):
    format_regex: Optional[str] = None
    constraints: Optional[list[dict]] = None
    description: Optional[str] = None


@router.put("/value-types/{name}")
async def update_value_type(
    name: str, request: UpdateValueTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """Editing a Value Type is the same governance tier as registering
    one. `base_type`/`name` aren't accepted here — see
    `ontology/value_types.py`'s `update_value_type` docstring.
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_value_type(core.pool, principal.tenant_id, name) is None:
        raise HTTPException(status_code=404, detail=f"unknown value type: {name}")
    try:
        return await ontology.update_value_type(
            core.pool,
            tenant_id=principal.tenant_id,
            name=name,
            format_regex=request.format_regex,
            constraints=request.constraints,
            description=request.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class SharedPropertyTypeRequest(BaseModel):
    api_name: str
    display_name: str
    value_type: str
    description: str = ""


@router.post("/shared-property-types", status_code=201)
async def create_shared_property_type(
    request: SharedPropertyTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """Registering a Shared Property Type is ontology governance, same
    tier as registering a Value Type. Distinct registry from
    `value_type`: this is the canonical *property* (api_name +
    display_name + description), not just the underlying data shape —
    see `shared_property_types.py`'s module docstring.
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_shared_property_type(core.pool, principal.tenant_id, request.api_name) is not None:
        raise HTTPException(status_code=409, detail=f"shared property type already exists: {request.api_name}")
    try:
        return await ontology.create_shared_property_type(
            core.pool,
            tenant_id=principal.tenant_id,
            api_name=request.api_name,
            display_name=request.display_name,
            value_type=request.value_type,
            description=request.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/shared-property-types")
async def list_shared_property_types(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    return await ontology.list_shared_property_types(core.pool, principal.tenant_id)


@router.get("/shared-property-types/{api_name}")
async def get_shared_property_type(api_name: str, principal: Principal = Depends(core.current_principal)) -> dict:
    shared_property_type = await ontology.get_shared_property_type(core.pool, principal.tenant_id, api_name)
    if shared_property_type is None:
        raise HTTPException(status_code=404, detail=f"unknown shared property type: {api_name}")
    return shared_property_type


class UpdateSharedPropertyTypeRequest(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None


@router.put("/shared-property-types/{api_name}")
async def update_shared_property_type(
    api_name: str, request: UpdateSharedPropertyTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """`value_type`/`api_name` aren't accepted here — see
    `ontology/shared_property_types.py`'s `update_shared_property_type`
    docstring.
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_shared_property_type(core.pool, principal.tenant_id, api_name) is None:
        raise HTTPException(status_code=404, detail=f"unknown shared property type: {api_name}")
    try:
        return await ontology.update_shared_property_type(
            core.pool,
            tenant_id=principal.tenant_id,
            api_name=api_name,
            display_name=request.display_name,
            description=request.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ActionTypeRequest(BaseModel):
    name: str
    target_object_type: Optional[str] = None
    target_interface: Optional[str] = None
    required_permission: str
    risk_level: str
    description: str
    parameters: list[dict] = []
    edits: list[dict] = []
    submission_criteria: list[dict] = []
    function_side_effect: Optional[str] = None
    writeback_dataset: Optional[str] = None
    # Function-backed Actions: exactly one of edits/edit_function, checked
    # in `ontology.create_action_type`, not here.
    edit_function: Optional[str] = None
    # Configure/Sections: purely a display grouping for the invocation
    # form, structurally validated in `ontology.create_action_type`.
    sections: list[dict] = []


@router.post("/action-types", status_code=201)
async def create_action_type(request: ActionTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    """Registering an Action Type is ontology governance, same tier as
    an Interface or a Value Type — the workspace's own `approve`
    permission. The no-code counterpart to writing a Python
    `register_apply_function`-decorated handler in `actions.py`: no
    deploy needed to add a new kind of write, just configuration —
    validated structurally here (real references, like a parameter's
    `value_type` or `target_object_type` existing, are checked at
    invocation time by `actions.request_generic_action`, not here).
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_action_type(core.pool, principal.tenant_id, request.name) is not None:
        raise HTTPException(status_code=409, detail=f"action type already exists: {request.name}")
    try:
        return await ontology.create_action_type(
            core.pool,
            tenant_id=principal.tenant_id,
            name=request.name,
            target_object_type=request.target_object_type,
            target_interface=request.target_interface,
            required_permission=request.required_permission,
            risk_level=request.risk_level,
            description=request.description,
            parameters=request.parameters,
            edits=request.edits,
            submission_criteria=request.submission_criteria,
            function_side_effect=request.function_side_effect,
            writeback_dataset=request.writeback_dataset,
            edit_function=request.edit_function,
            sections=request.sections,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/action-types")
async def list_action_types(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    return await ontology.list_action_types(core.pool, principal.tenant_id)


@router.get("/action-types/{name}")
async def get_action_type(name: str, principal: Principal = Depends(core.current_principal)) -> dict:
    action_type = await ontology.get_action_type(core.pool, principal.tenant_id, name)
    if action_type is None:
        raise HTTPException(status_code=404, detail=f"unknown action type: {name}")
    return action_type


@router.put("/action-types/{name}")
async def update_action_type(
    name: str, request: ActionTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """`create_action_type`'s SQL is already `ON CONFLICT (tenant_id,
    name) DO UPDATE` — this endpoint is a full-replace edit (matches the
    fact every column but `tenant_id`/`name` is already upsert-shaped
    underneath), gated on the same existence direction `create` isn't:
    404 if it's *not* there yet, instead of `create`'s 409 if it is.
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_action_type(core.pool, principal.tenant_id, name) is None:
        raise HTTPException(status_code=404, detail=f"unknown action type: {name}")
    try:
        return await ontology.create_action_type(
            core.pool,
            tenant_id=principal.tenant_id,
            name=name,
            target_object_type=request.target_object_type,
            target_interface=request.target_interface,
            required_permission=request.required_permission,
            risk_level=request.risk_level,
            description=request.description,
            parameters=request.parameters,
            edits=request.edits,
            submission_criteria=request.submission_criteria,
            function_side_effect=request.function_side_effect,
            writeback_dataset=request.writeback_dataset,
            edit_function=request.edit_function,
            sections=request.sections,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class InterfaceTypeRequest(BaseModel):
    name: str
    required_properties: list[str] = []
    required_actions: list[str] = []
    description: str = ""


@router.post("/interfaces", status_code=201)
async def create_interface_type(request: InterfaceTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    """Registering an Interface is ontology governance, same tier as
    RelationType registration and ObjectType-version publication — the
    workspace's own `approve` permission, not a per-ObjectType check
    (an interface isn't owned by any single ObjectType).
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_interface_type(core.pool, principal.tenant_id, request.name) is not None:
        raise HTTPException(status_code=409, detail=f"interface already exists: {request.name}")
    return await ontology.create_interface_type(
        core.pool,
        tenant_id=principal.tenant_id,
        name=request.name,
        required_properties=request.required_properties,
        required_actions=request.required_actions,
        description=request.description,
    )


@router.get("/interfaces")
async def list_interface_types(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """Same auth-only convention as `/ontology`/`/relation-types` —
    metadata about a definition, nothing for the PDP to check per-row.
    """
    return await ontology.list_interface_types(core.pool, principal.tenant_id)


@router.get("/interfaces/{name}")
async def get_interface_type(name: str, principal: Principal = Depends(core.current_principal)) -> dict:
    interface = await ontology.get_interface_type(core.pool, principal.tenant_id, name)
    if interface is None:
        raise HTTPException(status_code=404, detail=f"unknown interface: {name}")
    return interface


class UpdateInterfaceTypeRequest(BaseModel):
    required_properties: Optional[list[str]] = None
    required_actions: Optional[list[str]] = None
    description: Optional[str] = None


@router.put("/interfaces/{name}")
async def update_interface_type(
    name: str, request: UpdateInterfaceTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """`name` isn't accepted here — it's the key referenced from every
    ObjectType's `implements` list.
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_interface_type(core.pool, principal.tenant_id, name) is None:
        raise HTTPException(status_code=404, detail=f"unknown interface: {name}")
    try:
        return await ontology.update_interface_type(
            core.pool,
            tenant_id=principal.tenant_id,
            name=name,
            required_properties=request.required_properties,
            required_actions=request.required_actions,
            description=request.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/interfaces/{name}/objects")
async def list_interface_objects(name: str, principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """Polymorphic read: every instance of every ObjectType whose
    currently *published* `implements` names this interface, tagged with
    which ObjectType it came from. Reuses `_resolve_many` — the same read
    choke point (serving-store-first, masking applied, live scan fallback)
    every other list endpoint already goes through — once per conforming
    ObjectType, rather than a new read path of its own.
    """
    if await ontology.get_interface_type(core.pool, principal.tenant_id, name) is None:
        raise HTTPException(status_code=404, detail=f"unknown interface: {name}")
    results: list[dict] = []
    for object_type_name, fetch_fn in core.FETCH_FNS.items():
        object_type = await ontology.get_object_type(core.pool, core.OBJECT_TYPE_URNS[object_type_name])
        implements = (object_type.get("implements") or []) if object_type else []
        if name not in implements:
            continue
        rows = await core._resolve_many(object_type_name, principal.tenant_id, fetch_fn, principal=principal)
        for row in rows:
            row["_objectType"] = object_type_name
        results.extend(rows)
    return results


class MarkingRequest(BaseModel):
    name: str
    description: str = ""


@router.post("/markings", status_code=201)
async def create_marking(request: MarkingRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    """Registering a Marking is ontology governance, same tier as
    registering an Interface — the workspace's own `approve` permission.
    A marking is a *label registry entry*, not a grant: creating "PII"
    here doesn't give anyone clearance to it, `.../principals/.../grant`
    below does — same two-step shape Identity's own Project access
    already uses (create, then grant per-principal).
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_marking(core.pool, principal.tenant_id, request.name) is not None:
        raise HTTPException(status_code=409, detail=f"marking already exists: {request.name}")
    return await ontology.create_marking(
        core.pool, tenant_id=principal.tenant_id, name=request.name, description=request.description
    )


@router.get("/markings")
async def list_markings(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    return await ontology.list_markings(core.pool, principal.tenant_id)


@router.get("/markings/{name}")
async def get_marking(name: str, principal: Principal = Depends(core.current_principal)) -> dict:
    marking = await ontology.get_marking(core.pool, principal.tenant_id, name)
    if marking is None:
        raise HTTPException(status_code=404, detail=f"unknown marking: {name}")
    return marking


@router.post("/markings/{name}/principals/{principal_urn:path}/access/grant")
async def grant_marking_access(
    name: str, principal_urn: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """Grants `hold` on `marking:{name}` — the SpiceDB-level clearance
    `_authorize_markings` checks at read time. Governance-gated the same
    as creating the marking itself: only a workspace admin decides who
    holds a clearance label, same tier Identity's own project-access
    grant uses one level up the hierarchy.
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_marking(core.pool, principal.tenant_id, name) is None:
        raise HTTPException(status_code=404, detail=f"unknown marking: {name}")
    marking_urn = build_urn(principal.tenant_id, "global", "marking", name)
    await core.authz.write_relationship(
        resource_type="marking", resource_urn=marking_urn, relation="holder", subject_urn=principal_urn,
    )
    return {"status": "granted", "principalUrn": principal_urn, "marking": name}


@router.post("/markings/{name}/principals/{principal_urn:path}/access/revoke")
async def revoke_marking_access(
    name: str, principal_urn: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    await _authorize_ontology_governance(principal, workspace_id)
    if await ontology.get_marking(core.pool, principal.tenant_id, name) is None:
        raise HTTPException(status_code=404, detail=f"unknown marking: {name}")
    marking_urn = build_urn(principal.tenant_id, "global", "marking", name)
    await core.authz.delete_relationship(
        resource_type="marking", resource_urn=marking_urn, relation="holder", subject_urn=principal_urn,
    )
    return {"status": "revoked", "principalUrn": principal_urn, "marking": name}


class SetInstanceMarkingsRequest(BaseModel):
    markings: list[str]


@router.post("/objects/{object_type}/{instance_id}/markings")
async def set_instance_markings(
    object_type: str, instance_id: str, request: SetInstanceMarkingsRequest,
    principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """The other attachment point (alongside ObjectType-wide `markings`
    above): labeling one specific instance. Write-tier gated like any
    other mutation on this ObjectType — `_authorize_object_type` already
    also enforces any *ObjectType-wide* markings as part of that same
    check, so a principal locked out at the type level can't route
    around it by trying to label an individual instance either.
    """
    try:
        object_type_urn = await core._object_type_urn_for(object_type, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {object_type}")
    await core._authorize_object_type(principal, object_type_urn, "write")
    try:
        markings = await ontology.set_instance_markings(
            core.pool,
            object_type_urn=object_type_urn,
            tenant_id=principal.tenant_id,
            instance_id=instance_id,
            markings=request.markings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"objectType": object_type, "instanceId": instance_id, "markings": markings}


@router.get("/ontology/{name}/versions")
async def list_object_type_versions(
    name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> list[dict]:
    try:
        object_type_urn = await core._object_type_urn_for(name, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {name}")
    return await ontology.list_object_type_versions(core.pool, object_type_urn)


async def _link_object_type_to_project(object_type_urn: str, project_urn: Optional[str]) -> None:
    """After a publish (direct or via branch-review merge) sets a
    `project_urn`, reconciles the SpiceDB `parent_project` relationship
    that actually makes `object_type`'s `read/write/approve = ... +
    parent_project->...` union take effect.

    SpiceDB relationships are additive (`OPERATION_TOUCH`), so re-publishing an
    ObjectType under a different project — or unscoping it back to `None` —
    would leave stale `parent_project` edges in place, silently granting former
    project members standing access. Because `object_type.project_urn` is single-valued
    in Postgres, its SpiceDB relationship is kept single-valued.

    Order is write-new-then-delete-old (when scoping) so a mid-flight
    failure never removes the previous edge before the new one exists.
    On failure, best-effort restore of the pre-call edge set, then
    re-raise — PG publish has already committed; the caller surfaces 503
    so the client knows authz may be temporarily inconsistent rather
    than silently succeeding with a wrong ReBAC graph.
    """
    existing = await core.authz.read_relationships(
        resource_type="object_type", resource_urn=object_type_urn, relation="parent_project"
    )
    existing_urns = [relationship["subject"]["object"]["objectId"] for relationship in existing]

    async def _write(subject_urn: str) -> None:
        await core.authz.write_relationship(
            resource_type="object_type",
            resource_urn=object_type_urn,
            relation="parent_project",
            subject_type="project",
            subject_urn=subject_urn,
        )

    async def _delete(subject_urn: str) -> None:
        await core.authz.delete_relationship(
            resource_type="object_type",
            resource_urn=object_type_urn,
            relation="parent_project",
            subject_type="project",
            subject_urn=subject_urn,
        )

    async def _restore_snapshot() -> None:
        try:
            current = await core.authz.read_relationships(
                resource_type="object_type", resource_urn=object_type_urn, relation="parent_project"
            )
            current_urns = {relationship["subject"]["object"]["objectId"] for relationship in current}
            for urn in existing_urns:
                if urn not in current_urns:
                    await _write(urn)
            if project_urn is not None and project_urn not in existing_urns and project_urn in current_urns:
                await _delete(project_urn)
        except Exception:
            logger.exception(
                "failed to restore parent_project snapshot for %s after link error", object_type_urn
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
        logger.exception("SpiceDB parent_project reconcile failed for %s — attempting restore", object_type_urn)
        await _restore_snapshot()
        raise


@router.post("/ontology/{name}/versions/{version}/publish")
async def publish_object_type_version(
    name: str, version: int, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """Publishes transactional outbox event `knowledge.objecttype.published`
    and updates the live `object_type` row — the only thing that
    ever does, past its bootstrap-seeded state.
    """
    await _authorize_ontology_governance(principal, workspace_id)
    try:
        object_type_urn = await core._object_type_urn_for(name, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {name}")
    try:
        result = await ontology.publish_object_type_version(
            core.pool,
            object_type_urn=object_type_urn,
            version=version,
            identity_url=IDENTITY_URL,
            identity_token=_identity_validation_token(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        await _link_object_type_to_project(object_type_urn, result.get("project_urn"))
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"ObjectType version {version} published in Postgres, but SpiceDB "
                f"parent_project reconcile failed: {exc}"
            ),
        ) from exc
    return result


class CreateBranchRequest(BaseModel):
    branch_name: str
    property_mapping: Optional[dict] = None
    description: Optional[str] = None
    implements: Optional[list[str]] = None
    derived_properties: Optional[dict[str, str | dict]] = None
    project_urn: Optional[str] = None
    markings: Optional[list[str]] = None
    property_formats: Optional[dict[str, dict]] = None
    conditional_formats: Optional[dict[str, list]] = None
    property_types: Optional[dict[str, dict]] = None


class UpdateBranchDraftRequest(BaseModel):
    property_mapping: Optional[dict] = None
    description: Optional[str] = None
    implements: Optional[list[str]] = None
    derived_properties: Optional[dict[str, str | dict]] = None
    project_urn: Optional[str] = None
    markings: Optional[list[str]] = None
    property_formats: Optional[dict[str, dict]] = None
    conditional_formats: Optional[dict[str, list]] = None
    property_types: Optional[dict[str, dict]] = None


class ReviewBranchRequest(BaseModel):
    decision: str  # "approved" | "changes_requested"
    note: Optional[str] = None


@router.post("/ontology/{name}/branches", status_code=201)
async def create_branch(name: str, request: CreateBranchRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    """Branching + review: a named, ongoing line of ontology
    work — same `write`-tier (editor+) gate `propose_object_type_version`
    itself uses, one level up.
    """
    await _authorize_ontology_write(principal, workspace_id)
    try:
        object_type_urn = await core._object_type_urn_for(name, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {name}")
    try:
        return await ontology.create_branch(
            core.pool,
            object_type_urn=object_type_urn,
            branch_name=request.branch_name,
            created_by_urn=principal.urn,
            property_mapping=request.property_mapping,
            description=request.description,
            implements=request.implements,
            derived_properties=request.derived_properties,
            project_urn=request.project_urn,
            markings=request.markings,
            property_formats=request.property_formats,
            conditional_formats=request.conditional_formats,
            property_types=request.property_types,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ontology/{name}/branches")
async def list_branches(
    name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> list[dict]:
    try:
        object_type_urn = await core._object_type_urn_for(name, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {name}")
    return await ontology.list_branches(core.pool, object_type_urn)


@router.get("/ontology/{name}/branches/{branch_name}")
async def get_branch(
    name: str, branch_name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    try:
        object_type_urn = await core._object_type_urn_for(name, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {name}")
    branch = await ontology.get_branch(core.pool, object_type_urn, branch_name)
    if branch is None:
        raise HTTPException(status_code=404, detail=f"unknown branch: {branch_name}")
    return branch


@router.post("/ontology/{name}/branches/{branch_name}/draft")
async def update_branch_draft(
    name: str, branch_name: str, request: UpdateBranchDraftRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """Follow-up to a `changes_requested` review: proposes a new draft
    version and moves the branch's pointer to it — the branch stays open.
    """
    await _authorize_ontology_write(principal, workspace_id)
    try:
        object_type_urn = await core._object_type_urn_for(name, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {name}")
    try:
        return await ontology.update_branch_draft(
            core.pool,
            object_type_urn=object_type_urn,
            branch_name=branch_name,
            property_mapping=request.property_mapping,
            description=request.description,
            implements=request.implements,
            derived_properties=request.derived_properties,
            project_urn=request.project_urn,
            markings=request.markings,
            property_formats=request.property_formats,
            conditional_formats=request.conditional_formats,
            property_types=request.property_types,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ontology/{name}/branches/{branch_name}/review")
async def review_branch(
    name: str, branch_name: str, request: ReviewBranchRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """The merge gate — workspace `approve` (admin-only), same tier as
    `publish_object_type_version` directly. `decision == "approved"`
    merges by calling that exact function, so every existing validation
    (`implements`/`derived_properties`) and the `knowledge.objecttype.published`
    event still apply unchanged.
    """
    await _authorize_ontology_governance(principal, workspace_id)
    try:
        object_type_urn = await core._object_type_urn_for(name, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {name}")
    try:
        result = await ontology.review_branch(
            core.pool,
            object_type_urn=object_type_urn,
            branch_name=branch_name,
            reviewer_urn=principal.urn,
            decision=request.decision,
            note=request.note,
            identity_url=IDENTITY_URL,
            identity_token=_identity_validation_token(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if request.decision == "approved":
        object_type = await ontology.get_object_type(core.pool, object_type_urn)
        try:
            await _link_object_type_to_project(object_type_urn, object_type.get("project_urn") if object_type else None)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"branch {branch_name!r} merged in Postgres, but SpiceDB "
                    f"parent_project reconcile failed: {exc}"
                ),
            ) from exc
    return result


@router.get("/ontology/{name}/branches/{branch_name}/reviews")
async def list_branch_reviews(
    name: str, branch_name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> list[dict]:
    try:
        object_type_urn = await core._object_type_urn_for(name, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {name}")
    branch = await ontology.get_branch(core.pool, object_type_urn, branch_name)
    if branch is None:
        raise HTTPException(status_code=404, detail=f"unknown branch: {branch_name}")
    return await ontology.list_branch_reviews(core.pool, branch["id"])


def _validate_resource_type(resource_type: str) -> None:
    if resource_type not in ontology.ALLOWED_RESOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown resource_type: {resource_type!r} (expected one of {sorted(ontology.ALLOWED_RESOURCE_TYPES)})",
        )


class CreateResourceBranchRequest(BaseModel):
    branch_name: str
    proposed_definition: dict


class UpdateResourceBranchDraftRequest(BaseModel):
    proposed_definition: dict


@router.post("/ontology-resources/{resource_type}/{resource_name}/branches", status_code=201)
async def create_resource_branch(
    resource_type: str, resource_name: str, request: CreateResourceBranchRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """Generic branch/review for the 4 registries that aren't ObjectType
    (`ontology/resource_branching.py`) — same `write`-tier gate
    `create_branch` (ObjectType's own version) already uses. Unlike
    ObjectType, there's no structural validation of `proposed_definition`
    at this point — it's a freeform dict until review time, when it's
    validated for real by calling straight through to the target
    registry's `create_*`/`update_*`.
    """
    _validate_resource_type(resource_type)
    await _authorize_ontology_write(principal, workspace_id)
    try:
        return await ontology.create_resource_branch(
            core.pool,
            resource_type=resource_type,
            resource_name=resource_name,
            branch_name=request.branch_name,
            created_by_urn=principal.urn,
            tenant_id=principal.tenant_id,
            proposed_definition=request.proposed_definition,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ontology-resources/{resource_type}/{resource_name}/branches")
async def list_resource_branches(
    resource_type: str, resource_name: str, principal: Principal = Depends(core.current_principal)
) -> list[dict]:
    _validate_resource_type(resource_type)
    return await ontology.list_resource_branches(
        core.pool, resource_type=resource_type, resource_name=resource_name, tenant_id=principal.tenant_id
    )


@router.get("/ontology-resources/{resource_type}/{resource_name}/branches/{branch_name}")
async def get_resource_branch(
    resource_type: str, resource_name: str, branch_name: str, principal: Principal = Depends(core.current_principal)
) -> dict:
    _validate_resource_type(resource_type)
    branch = await ontology.get_resource_branch(
        core.pool, resource_type=resource_type, resource_name=resource_name, branch_name=branch_name, tenant_id=principal.tenant_id
    )
    if branch is None:
        raise HTTPException(status_code=404, detail=f"unknown branch: {branch_name}")
    return branch


@router.post("/ontology-resources/{resource_type}/{resource_name}/branches/{branch_name}/draft")
async def update_resource_branch_draft(
    resource_type: str,
    resource_name: str,
    branch_name: str,
    request: UpdateResourceBranchDraftRequest,
    principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace),
) -> dict:
    _validate_resource_type(resource_type)
    await _authorize_ontology_write(principal, workspace_id)
    try:
        return await ontology.update_resource_branch_draft(
            core.pool,
            resource_type=resource_type,
            resource_name=resource_name,
            branch_name=branch_name,
            tenant_id=principal.tenant_id,
            proposed_definition=request.proposed_definition,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ontology-resources/{resource_type}/{resource_name}/branches/{branch_name}/review")
async def review_resource_branch(
    resource_type: str,
    resource_name: str,
    branch_name: str,
    request: ReviewBranchRequest,
    principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """The merge gate — workspace `approve` (admin-only), same tier as
    `review_branch` (ObjectType's own version). `decision == "approved"`
    calls straight through to the target registry's real `create_*`/
    `update_*`, so every existing structural validation those functions
    already do still applies unchanged.
    """
    _validate_resource_type(resource_type)
    await _authorize_ontology_governance(principal, workspace_id)
    try:
        return await ontology.review_resource_branch(
            core.pool,
            resource_type=resource_type,
            resource_name=resource_name,
            branch_name=branch_name,
            reviewer_urn=principal.urn,
            decision=request.decision,
            note=request.note,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ontology-resources/{resource_type}/{resource_name}/branches/{branch_name}/reviews")
async def list_resource_branch_reviews(
    resource_type: str, resource_name: str, branch_name: str, principal: Principal = Depends(core.current_principal)
) -> list[dict]:
    _validate_resource_type(resource_type)
    branch = await ontology.get_resource_branch(
        core.pool, resource_type=resource_type, resource_name=resource_name, branch_name=branch_name, tenant_id=principal.tenant_id
    )
    if branch is None:
        raise HTTPException(status_code=404, detail=f"unknown branch: {branch_name}")
    return await ontology.list_resource_branch_reviews(core.pool, branch["id"])


@router.get("/query-log")
async def get_query_log(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """Read surface for the anonymized query log, so it's genuinely
    inspectable rather than write-only. Auth-only, tenant-scoped.
    """
    return await query_log.list_recent(core.pool, principal.tenant_id)


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
        raise HTTPException(status_code=404, detail=f"unknown glossary term: {term!r}")
    return result


@router.get("/actions")
async def list_actions(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """Read surface for both the two hardcoded `actions.ACTION_DEFINITIONS`
    entries and every registered declarative Action Type — same auth-only
    convention as `/ontology/{name}`/`/relation-types` (metadata about a
    definition, not an instance read; nothing for the PDP to check per-row).
    Exists so mandatory descriptions are actually queryable (including by
    an agent tool-compiler), not just inert dict values inside `actions.py`.
    """
    hardcoded = [{"name": name, **definition} for name, definition in actions.ACTION_DEFINITIONS.items()]
    declared = [
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
        }
        for action_type in await ontology.list_action_types(core.pool, principal.tenant_id)
    ]
    return hardcoded + declared


@router.get("/actions/{name}")
async def get_action(name: str, principal: Principal = Depends(core.current_principal)) -> dict:
    definition = await actions._get_action_definition(core.pool, principal.tenant_id, name)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"unknown Action: {name}")
    public = {k: v for k, v in definition.items() if k != "_declarative"}
    return {"name": name, **public}


class RelationTypeRequest(BaseModel):
    name: str
    source_object_type: str
    target_object_type: str
    source_property: str = ""
    target_property: str
    cardinality: str
    storage_kind: str = "foreign_key"
    join_dataset_urn: Optional[str] = None
    join_source_column: Optional[str] = None
    join_target_column: Optional[str] = None
    mid_object_type: Optional[str] = None
    mid_source_property: Optional[str] = None
    mid_target_property: Optional[str] = None


@router.get("/relation-types")
async def list_relation_types(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """Same auth-only convention as `/ontology/{name}` — metadata, not
    instance data, so nothing for the PDP to check per-row.
    """
    return await ontology.list_relation_types(core.pool, principal.tenant_id)


class UpdateRelationTypeRequest(BaseModel):
    target_property: Optional[str] = None
    cardinality: Optional[str] = None
    storage_kind: Optional[str] = None
    join_dataset_urn: Optional[str] = None
    join_source_column: Optional[str] = None
    join_target_column: Optional[str] = None
    mid_object_type: Optional[str] = None
    mid_source_property: Optional[str] = None
    mid_target_property: Optional[str] = None


@router.put("/relation-types/{name}")
async def update_relation_type(
    name: str, request: UpdateRelationTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """Source/target ObjectType and `source_property` aren't accepted
    here — they're the structural identity of the link. See
    `ontology/relation_types.py`'s `update_relation_type` docstring.
    """
    await _authorize_ontology_governance(principal, workspace_id)
    urn = ontology.relation_type_urn(principal.tenant_id, workspace_id, name)
    if await ontology.get_relation_type(core.pool, urn) is None:
        raise HTTPException(status_code=404, detail=f"unknown RelationType: {name}")
    try:
        return await ontology.update_relation_type(
            core.pool,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            name=name,
            target_property=request.target_property,
            cardinality=request.cardinality,
            storage_kind=request.storage_kind,
            join_dataset_urn=request.join_dataset_urn,
            join_source_column=request.join_source_column,
            join_target_column=request.join_target_column,
            mid_object_type=request.mid_object_type,
            mid_source_property=request.mid_source_property,
            mid_target_property=request.mid_target_property,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/relation-types/{name}")
async def get_relation_type(name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    urn = ontology.relation_type_urn(principal.tenant_id, workspace_id, name)
    relation_type = await ontology.get_relation_type(core.pool, urn)
    if relation_type is None:
        raise HTTPException(status_code=404, detail=f"unknown RelationType: {name}")
    return relation_type


@router.post("/relation-types", status_code=201)
async def create_relation_type(request: RelationTypeRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    """Registering a new RelationType is an ontology governance action, not
    a data read/write — gated on the workspace's own `approve` permission
    (admin-only, the same tier that decides high-risk Actions), not
    `_authorize_object_type` (there's no single ObjectType this belongs to;
    it connects two).
    """
    decision = await core.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=ontology.workspace_urn(principal.tenant_id, workspace_id),
        permission="approve",
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)

    urn = ontology.relation_type_urn(principal.tenant_id, workspace_id, request.name)
    if await ontology.get_relation_type(core.pool, urn) is not None:
        raise HTTPException(status_code=409, detail=f"RelationType already exists: {request.name}")

    try:
        return await ontology.create_relation_type(
            core.pool,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            name=request.name,
            source_object_type=request.source_object_type,
            target_object_type=request.target_object_type,
            source_property=request.source_property,
            target_property=request.target_property,
            cardinality=request.cardinality,
            storage_kind=request.storage_kind,
            join_dataset_urn=request.join_dataset_urn,
            join_source_column=request.join_source_column,
            join_target_column=request.join_target_column,
            mid_object_type=request.mid_object_type,
            mid_source_property=request.mid_source_property,
            mid_target_property=request.mid_target_property,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ObjectTypeGroupRequest(BaseModel):
    name: str
    description: str = ""
    object_types: list[str] = []


@router.post("/object-type-groups", status_code=201)
async def create_object_type_group(
    request: ObjectTypeGroupRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """A purely navigational registry — same governance tier as
    Interfaces/Markings/Value Types, not its own permission concept.
    """
    await _authorize_ontology_governance(principal, workspace_id)
    try:
        return await ontology.create_object_type_group(
            core.pool,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            name=request.name,
            description=request.description,
            object_types=request.object_types,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/object-type-groups")
async def list_object_type_groups(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    return await ontology.list_object_type_groups(core.pool, principal.tenant_id)


class ObjectSetRequest(BaseModel):
    name: str
    object_type: str
    definition: dict
    display_name: str = ""
    description: str = ""
    lifecycle_status: str = "experimental"
    visibility: str = "normal"


class UpdateObjectSetRequest(BaseModel):
    definition: Optional[dict] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    lifecycle_status: Optional[str] = None
    visibility: Optional[str] = None


@router.get("/object-sets")
async def list_object_sets(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    return await ontology.list_object_sets(core.pool, principal.tenant_id)


@router.post("/object-sets", status_code=201)
async def create_object_set(request: ObjectSetRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    await _authorize_ontology_governance(principal, workspace_id)
    try:
        return await ontology.create_object_set(
            core.pool,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            name=request.name,
            object_type=request.object_type,
            definition=request.definition,
            display_name=request.display_name,
            description=request.description,
            lifecycle_status=request.lifecycle_status,
            visibility=request.visibility,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/object-sets/{name}")
async def get_object_set(name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    urn = ontology.object_set_urn(principal.tenant_id, workspace_id, name)
    row = await ontology.get_object_set(core.pool, urn)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown object set: {name}")
    return row


@router.put("/object-sets/{name}")
async def update_object_set(
    name: str, request: UpdateObjectSetRequest, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    await _authorize_ontology_governance(principal, workspace_id)
    try:
        return await ontology.update_object_set(
            core.pool,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            name=name,
            definition=request.definition,
            display_name=request.display_name,
            description=request.description,
            lifecycle_status=request.lifecycle_status,
            visibility=request.visibility,
        )
    except ValueError as exc:
        status = 404 if "unknown" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.get("/object-sets/{name}/objects")
async def evaluate_object_set(name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)) -> dict:
    """Evaluate the set filter against live instances — PDP-gated via `_resolve_many`."""
    urn = ontology.object_set_urn(principal.tenant_id, workspace_id, name)
    obj_set = await ontology.get_object_set(core.pool, urn)
    if obj_set is None:
        raise HTTPException(status_code=404, detail=f"unknown object set: {name}")
    if obj_set.get("visibility") == "hidden":
        # Still readable by admins with ontology approve; others get 404.
        try:
            await _authorize_ontology_governance(principal, workspace_id)
        except HTTPException:
            raise HTTPException(status_code=404, detail=f"unknown object set: {name}")

    object_type = obj_set["object_type_urn"].rsplit(":", 1)[-1]
    handle = await core._type_handle(object_type, principal.tenant_id)
    if handle is None:
        raise HTTPException(status_code=404, detail=f"backing ObjectType {object_type!r} missing")
    await core._authorize_object_type(principal, handle["urn"], "read")
    ot = await ontology.get_object_type(core.pool, obj_set["object_type_urn"])
    rows = await core._resolve_many(
        object_type, principal.tenant_id, handle["fetch_fn"], principal=principal,
    )
    mapping = (ot or {}).get("property_mapping") or {}
    matched = [r for r in rows if ontology.matches_predicates(r, obj_set["definition"], mapping)]
    for row in matched:
        row["title"] = ontology.title_of(row, ot)
    return {"object_set": name, "object_type": object_type, "count": len(matched), "items": matched}

