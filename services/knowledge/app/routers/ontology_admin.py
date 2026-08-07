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
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from pyiceberg.exceptions import NoSuchTableError

from holon_common import Principal, build_urn, issue_token

from .. import actions, catalog, glossary, ontology, query_log, resolver
from .. import core

router = APIRouter()

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
    return issue_token(principal, core.JWT_SECRET, ttl_seconds=60)


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


@router.get("/ontology/{name}")
async def get_ontology_definition(name: str, principal: Principal = Depends(core.current_principal)) -> dict:
    """Inspects an ObjectType *definition* — property mapping, computed
    classification — as opposed to `/objects/{name}` which resolves its
    *instances*. Metadata, not data: gated by authentication only, like
    `/catalog/datasets`, not by the PDP (row/column security has
    nothing to enforce on a definition with no rows).
    """
    object_type_urn = core.OBJECT_TYPE_URNS.get(name) or ontology.object_type_urn(principal.tenant_id, core.WORKSPACE_ID, name)
    object_type = await ontology.get_object_type(core.pool, object_type_urn)
    if object_type is None:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {name}")
    return object_type


async def _authorize_ontology_governance(principal: Principal) -> None:
    """Ontology lifecycle changes (versioning/publication) are a
    governance action, same tier as `create_relation_type` — the
    workspace's own `approve` permission (admin-only), not
    `_authorize_object_type` (there's no read/write of instance data
    happening here).
    """
    decision = await core.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=ontology.workspace_urn(principal.tenant_id, core.WORKSPACE_ID),
        permission="approve",
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)


async def _authorize_ontology_write(principal: Principal) -> None:
    """Branch creation is a lighter-weight governance action than
    publishing — workspace `write` (editor+), not `approve` (admin-only).
    This is what makes review meaningful: the same role separation
    Actions already rely on for `action_approval` (an editor can request,
    only an admin can decide), not a same-URN check.
    """
    decision = await core.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=ontology.workspace_urn(principal.tenant_id, core.WORKSPACE_ID),
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


@router.post("/object-types", status_code=201)
async def create_object_type(request: CreateObjectTypeRequest, principal: Principal = Depends(core.current_principal)) -> dict:
    """The self-serve half of the no-code connector: an already-synced
    Dataset (see `connectivity`'s `/sources`) becomes a real, browsable
    ObjectType — same governance tier as publishing a version
    (`_authorize_ontology_governance`, admin-only `approve`) since this
    takes effect immediately, no draft/publish step, the same way the six
    boot-seeded types are immediately live.

    Deliberately narrower than the full ontology lifecycle: no
    interfaces/derived-properties/markings/branching at creation time —
    those are exactly what `POST /ontology/{name}/versions` +
    publish are for, and a self-serve type can grow into them later the
    same way a seeded type does. What's new here is *existence*: making
    a type reachable at all without a code change, which the versioning
    endpoints alone can't do (they only ever version a type that's
    already there).
    """
    await _authorize_ontology_governance(principal)
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
            workspace_id=core.WORKSPACE_ID,
            name=request.name,
            source_dataset_urn=request.source_dataset_urn,
            property_mapping=request.property_mapping,
            description=request.description,
            column_classification=request.column_classification,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Without this, `_authorize_object_type` denies everyone, including
    # the creator — `object_type.read/write/approve` all reduce to
    # `parent_workspace->...`, the same relationship `authz_seed.py`
    # writes for the six boot-seeded types at startup.
    await core.authz.write_relationship(
        resource_type="object_type",
        resource_urn=object_type["urn"],
        relation="parent_workspace",
        subject_type="workspace",
        subject_urn=ontology.workspace_urn(principal.tenant_id, core.WORKSPACE_ID),
    )
    return object_type


class ProposeObjectTypeVersionRequest(BaseModel):
    property_mapping: Optional[dict] = None
    description: Optional[str] = None
    implements: Optional[list[str]] = None
    derived_properties: Optional[dict[str, str]] = None
    project_urn: Optional[str] = None
    markings: Optional[list[str]] = None
    property_formats: Optional[dict[str, dict]] = None
    property_types: Optional[dict[str, dict]] = None


@router.post("/ontology/{name}/versions", status_code=201)
async def propose_object_type_version(
    name: str, request: ProposeObjectTypeVersionRequest, principal: Principal = Depends(core.current_principal)
) -> dict:
    """Ontology lifecycle versioning. Creates a `draft` — never
    touches the live definition every other read path uses until
    `POST /ontology/{name}/versions/{version}/publish` says otherwise.
    """
    await _authorize_ontology_governance(principal)
    try:
        object_type_urn = await core._object_type_urn_for(name)
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
            property_types=request.property_types,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ValueTypeRequest(BaseModel):
    name: str
    base_type: str
    format_regex: Optional[str] = None
    description: str = ""


@router.post("/value-types", status_code=201)
async def create_value_type(request: ValueTypeRequest, principal: Principal = Depends(core.current_principal)) -> dict:
    """Registering a Value Type is ontology governance, same tier as
    registering an Interface or a Marking — the workspace's own
    `approve` permission. A separate registry from `property_formats`
    (display-only) — this one is real data typing, referenced by
    `property_types` (below) and by declarative Action parameters.
    """
    await _authorize_ontology_governance(principal)
    if await ontology.get_value_type(core.pool, principal.tenant_id, request.name) is not None:
        raise HTTPException(status_code=409, detail=f"value type already exists: {request.name}")
    try:
        return await ontology.create_value_type(
            core.pool,
            tenant_id=principal.tenant_id,
            name=request.name,
            base_type=request.base_type,
            format_regex=request.format_regex,
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


class SharedPropertyTypeRequest(BaseModel):
    api_name: str
    display_name: str
    value_type: str
    description: str = ""


@router.post("/shared-property-types", status_code=201)
async def create_shared_property_type(
    request: SharedPropertyTypeRequest, principal: Principal = Depends(core.current_principal)
) -> dict:
    """Registering a Shared Property Type is ontology governance, same
    tier as registering a Value Type. Distinct registry from
    `value_type`: this is the canonical *property* (api_name +
    display_name + description), not just the underlying data shape —
    see `shared_property_types.py`'s module docstring.
    """
    await _authorize_ontology_governance(principal)
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


class ActionTypeRequest(BaseModel):
    name: str
    target_object_type: str
    required_permission: str
    risk_level: str
    description: str
    parameters: list[dict] = []
    edits: list[dict]
    submission_criteria: list[dict] = []
    function_side_effect: Optional[str] = None
    writeback_dataset: Optional[str] = None


@router.post("/action-types", status_code=201)
async def create_action_type(request: ActionTypeRequest, principal: Principal = Depends(core.current_principal)) -> dict:
    """Registering an Action Type is ontology governance, same tier as
    an Interface or a Value Type — the workspace's own `approve`
    permission. The no-code counterpart to writing a Python
    `register_apply_function`-decorated handler in `actions.py`: no
    deploy needed to add a new kind of write, just configuration —
    validated structurally here (real references, like a parameter's
    `value_type` or `target_object_type` existing, are checked at
    invocation time by `actions.request_generic_action`, not here).
    """
    await _authorize_ontology_governance(principal)
    if await ontology.get_action_type(core.pool, principal.tenant_id, request.name) is not None:
        raise HTTPException(status_code=409, detail=f"action type already exists: {request.name}")
    try:
        return await ontology.create_action_type(
            core.pool,
            tenant_id=principal.tenant_id,
            name=request.name,
            target_object_type=request.target_object_type,
            required_permission=request.required_permission,
            risk_level=request.risk_level,
            description=request.description,
            parameters=request.parameters,
            edits=request.edits,
            submission_criteria=request.submission_criteria,
            function_side_effect=request.function_side_effect,
            writeback_dataset=request.writeback_dataset,
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


class InterfaceTypeRequest(BaseModel):
    name: str
    required_properties: list[str] = []
    required_actions: list[str] = []
    description: str = ""


@router.post("/interfaces", status_code=201)
async def create_interface_type(request: InterfaceTypeRequest, principal: Principal = Depends(core.current_principal)) -> dict:
    """Registering an Interface is ontology governance, same tier as
    RelationType registration and ObjectType-version publication — the
    workspace's own `approve` permission, not a per-ObjectType check
    (an interface isn't owned by any single ObjectType).
    """
    await _authorize_ontology_governance(principal)
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
async def create_marking(request: MarkingRequest, principal: Principal = Depends(core.current_principal)) -> dict:
    """Registering a Marking is ontology governance, same tier as
    registering an Interface — the workspace's own `approve` permission.
    A marking is a *label registry entry*, not a grant: creating "PII"
    here doesn't give anyone clearance to it, `.../principals/.../grant`
    below does — same two-step shape Identity's own Project access
    already uses (create, then grant per-principal).
    """
    await _authorize_ontology_governance(principal)
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
    name: str, principal_urn: str, principal: Principal = Depends(core.current_principal)
) -> dict:
    """Grants `hold` on `marking:{name}` — the SpiceDB-level clearance
    `_authorize_markings` checks at read time. Governance-gated the same
    as creating the marking itself: only a workspace admin decides who
    holds a clearance label, same tier Identity's own project-access
    grant uses one level up the hierarchy.
    """
    await _authorize_ontology_governance(principal)
    if await ontology.get_marking(core.pool, principal.tenant_id, name) is None:
        raise HTTPException(status_code=404, detail=f"unknown marking: {name}")
    marking_urn = build_urn(principal.tenant_id, "global", "marking", name)
    await core.authz.write_relationship(
        resource_type="marking", resource_urn=marking_urn, relation="holder", subject_urn=principal_urn,
    )
    return {"status": "granted", "principalUrn": principal_urn, "marking": name}


@router.post("/markings/{name}/principals/{principal_urn:path}/access/revoke")
async def revoke_marking_access(
    name: str, principal_urn: str, principal: Principal = Depends(core.current_principal)
) -> dict:
    await _authorize_ontology_governance(principal)
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
    object_type: str, instance_id: str, request: SetInstanceMarkingsRequest, principal: Principal = Depends(core.current_principal)
) -> dict:
    """The other attachment point (alongside ObjectType-wide `markings`
    above): labeling one specific instance. Write-tier gated like any
    other mutation on this ObjectType — `_authorize_object_type` already
    also enforces any *ObjectType-wide* markings as part of that same
    check, so a principal locked out at the type level can't route
    around it by trying to label an individual instance either.
    """
    try:
        object_type_urn = await core._object_type_urn_for(object_type)
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
async def list_object_type_versions(name: str, principal: Principal = Depends(core.current_principal)) -> list[dict]:
    try:
        object_type_urn = await core._object_type_urn_for(name)
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
    in Postgres, its SpiceDB relationship is kept single-valued by deleting all
    existing `parent_project` edges first before writing the new one (if any).
    """
    existing = await core.authz.read_relationships(
        resource_type="object_type", resource_urn=object_type_urn, relation="parent_project"
    )
    for relationship in existing:
        await core.authz.delete_relationship(
            resource_type="object_type",
            resource_urn=object_type_urn,
            relation="parent_project",
            subject_type="project",
            subject_urn=relationship["subject"]["object"]["objectId"],
        )
    if project_urn is not None:
        await core.authz.write_relationship(
            resource_type="object_type",
            resource_urn=object_type_urn,
            relation="parent_project",
            subject_type="project",
            subject_urn=project_urn,
        )


@router.post("/ontology/{name}/versions/{version}/publish")
async def publish_object_type_version(
    name: str, version: int, principal: Principal = Depends(core.current_principal)
) -> dict:
    """Publishes transactional outbox event `knowledge.objecttype.published`
    and updates the live `object_type` row — the only thing that
    ever does, past its bootstrap-seeded state.
    """
    await _authorize_ontology_governance(principal)
    try:
        object_type_urn = await core._object_type_urn_for(name)
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
    await _link_object_type_to_project(object_type_urn, result.get("project_urn"))
    return result


class CreateBranchRequest(BaseModel):
    branch_name: str
    property_mapping: Optional[dict] = None
    description: Optional[str] = None
    implements: Optional[list[str]] = None
    derived_properties: Optional[dict[str, str]] = None
    project_urn: Optional[str] = None
    markings: Optional[list[str]] = None


class UpdateBranchDraftRequest(BaseModel):
    property_mapping: Optional[dict] = None
    description: Optional[str] = None
    implements: Optional[list[str]] = None
    derived_properties: Optional[dict[str, str]] = None
    project_urn: Optional[str] = None
    markings: Optional[list[str]] = None


class ReviewBranchRequest(BaseModel):
    decision: str  # "approved" | "changes_requested"
    note: Optional[str] = None


@router.post("/ontology/{name}/branches", status_code=201)
async def create_branch(name: str, request: CreateBranchRequest, principal: Principal = Depends(core.current_principal)) -> dict:
    """Branching + review: a named, ongoing line of ontology
    work — same `write`-tier (editor+) gate `propose_object_type_version`
    itself uses, one level up.
    """
    await _authorize_ontology_write(principal)
    try:
        object_type_urn = await core._object_type_urn_for(name)
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
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ontology/{name}/branches")
async def list_branches(name: str, principal: Principal = Depends(core.current_principal)) -> list[dict]:
    try:
        object_type_urn = await core._object_type_urn_for(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {name}")
    return await ontology.list_branches(core.pool, object_type_urn)


@router.get("/ontology/{name}/branches/{branch_name}")
async def get_branch(name: str, branch_name: str, principal: Principal = Depends(core.current_principal)) -> dict:
    try:
        object_type_urn = await core._object_type_urn_for(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {name}")
    branch = await ontology.get_branch(core.pool, object_type_urn, branch_name)
    if branch is None:
        raise HTTPException(status_code=404, detail=f"unknown branch: {branch_name}")
    return branch


@router.post("/ontology/{name}/branches/{branch_name}/draft")
async def update_branch_draft(
    name: str, branch_name: str, request: UpdateBranchDraftRequest, principal: Principal = Depends(core.current_principal)
) -> dict:
    """Follow-up to a `changes_requested` review: proposes a new draft
    version and moves the branch's pointer to it — the branch stays open.
    """
    await _authorize_ontology_write(principal)
    try:
        object_type_urn = await core._object_type_urn_for(name)
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
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ontology/{name}/branches/{branch_name}/review")
async def review_branch(
    name: str, branch_name: str, request: ReviewBranchRequest, principal: Principal = Depends(core.current_principal)
) -> dict:
    """The merge gate — workspace `approve` (admin-only), same tier as
    `publish_object_type_version` directly. `decision == "approved"`
    merges by calling that exact function, so every existing validation
    (`implements`/`derived_properties`) and the `knowledge.objecttype.published`
    event still apply unchanged.
    """
    await _authorize_ontology_governance(principal)
    try:
        object_type_urn = await core._object_type_urn_for(name)
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
        await _link_object_type_to_project(object_type_urn, object_type.get("project_urn"))
    return result


@router.get("/ontology/{name}/branches/{branch_name}/reviews")
async def list_branch_reviews(name: str, branch_name: str, principal: Principal = Depends(core.current_principal)) -> list[dict]:
    try:
        object_type_urn = await core._object_type_urn_for(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {name}")
    branch = await ontology.get_branch(core.pool, object_type_urn, branch_name)
    if branch is None:
        raise HTTPException(status_code=404, detail=f"unknown branch: {branch_name}")
    return await ontology.list_branch_reviews(core.pool, branch["id"])


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
            "required_permission": action_type["required_permission"],
            "risk_level": action_type["risk_level"],
            "description": action_type["description"],
            "function_side_effect": action_type.get("function_side_effect"),
            "writeback_dataset": action_type.get("writeback_dataset"),
            "parameters": action_type["parameters"],
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
    source_property: str
    cardinality: str


@router.get("/relation-types")
async def list_relation_types(principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """Same auth-only convention as `/ontology/{name}` — metadata, not
    instance data, so nothing for the PDP to check per-row.
    """
    return await ontology.list_relation_types(core.pool, principal.tenant_id)


@router.get("/relation-types/{name}")
async def get_relation_type(name: str, principal: Principal = Depends(core.current_principal)) -> dict:
    urn = ontology.relation_type_urn(principal.tenant_id, core.WORKSPACE_ID, name)
    relation_type = await ontology.get_relation_type(core.pool, urn)
    if relation_type is None:
        raise HTTPException(status_code=404, detail=f"unknown RelationType: {name}")
    return relation_type


@router.post("/relation-types", status_code=201)
async def create_relation_type(request: RelationTypeRequest, principal: Principal = Depends(core.current_principal)) -> dict:
    """Registering a new RelationType is an ontology governance action, not
    a data read/write — gated on the workspace's own `approve` permission
    (admin-only, the same tier that decides high-risk Actions), not
    `_authorize_object_type` (there's no single ObjectType this belongs to;
    it connects two).
    """
    decision = await core.authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=ontology.workspace_urn(principal.tenant_id, core.WORKSPACE_ID),
        permission="approve",
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)

    urn = ontology.relation_type_urn(principal.tenant_id, core.WORKSPACE_ID, request.name)
    if await ontology.get_relation_type(core.pool, urn) is not None:
        raise HTTPException(status_code=409, detail=f"RelationType already exists: {request.name}")

    try:
        return await ontology.create_relation_type(
            core.pool,
            tenant_id=principal.tenant_id,
            workspace_id=core.WORKSPACE_ID,
            name=request.name,
            source_object_type=request.source_object_type,
            target_object_type=request.target_object_type,
            source_property=request.source_property,
            cardinality=request.cardinality,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
