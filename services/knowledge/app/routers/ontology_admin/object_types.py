"""ObjectType create, versions, publish, reindex, and branches."""

from __future__ import annotations

import asyncio
import logging
import os
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
    takes effect immediately, no draft/publish step, the same way demo
    ObjectTypes created via `POST /object-types` are immediately live.

    Creation itself is existence + mapping only; branching, interfaces,
    markings, and the rest of the lifecycle attach afterward via
    `POST /ontology/{name}/versions` (+ publish) / branches — same path
    a seeded type uses.
    """
    await _authorize_ontology_governance(principal, workspace_id)
    if not request.property_mapping:
        raise HolonError.invalid_argument('InvalidPropertyMapping', "property_mapping must name at least one property")
    bad_values = set(request.column_classification.values()) - _ALLOWED_CLASSIFICATIONS
    if bad_values:
        raise HolonError.invalid_argument('InvalidClassification', f"unknown classification value(s) {sorted(bad_values)} (expected one of {sorted(_ALLOWED_CLASSIFICATIONS)})",
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
        raise HolonError.from_http(
            409 if "already exists" in str(exc) else 400,
            str(exc),
            error_name="ObjectTypeAlreadyExists" if "already exists" in str(exc) else "ObjectTypeValidationFailed",
        ) from exc

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
        raise HolonError.unavailable('Unavailable', f"failed to seed object_type authz relationship: {exc}",) from exc
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
    link_constraint_bindings: Optional[dict[str, dict[str, str]]] = None
    interface_property_bindings: Optional[dict[str, dict[str, str]]] = None
    primary_key: Optional[str] = None
    title_key: Optional[str] = None
    plural_display_name: Optional[str] = None
    lifecycle_status: Optional[str] = None
    visibility: Optional[str] = None
    icon: Optional[str] = None
    deprecation_reason: Optional[str] = None
    deprecation_deadline: Optional[str] = None
    replacement_urn: Optional[str] = None


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
        raise HolonError.not_found('ObjectTypeNotFound', f"unknown ObjectType: {name}")
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
            link_constraint_bindings=request.link_constraint_bindings,
            interface_property_bindings=request.interface_property_bindings,
            primary_key=request.primary_key,
            title_key=request.title_key,
            plural_display_name=request.plural_display_name,
            lifecycle_status=request.lifecycle_status,
            visibility=request.visibility,
            icon=request.icon,
            deprecation_reason=request.deprecation_reason,
            deprecation_deadline=request.deprecation_deadline,
            replacement_urn=request.replacement_urn,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('ObjectTypeValidationFailed', str(exc)) from exc


@router.get("/ontology/{name}/versions")
async def list_object_type_versions(
    name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> list[dict]:
    try:
        object_type_urn = await core._object_type_urn_for(name, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HolonError.not_found('ObjectTypeNotFound', f"unknown ObjectType: {name}")
    return await ontology.list_object_type_versions(core.pool, object_type_urn)


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
        raise HolonError.not_found('ObjectTypeNotFound', f"unknown ObjectType: {name}")
    try:
        result = await ontology.publish_object_type_version(
            core.pool,
            object_type_urn=object_type_urn,
            version=version,
            identity_url=IDENTITY_URL,
            identity_token=_identity_validation_token(),
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('ObjectTypeValidationFailed', str(exc)) from exc
    try:
        await _link_object_type_to_project(object_type_urn, result.get("project_urn"))
    except Exception as exc:
        raise HolonError.unavailable('Unavailable', (
                f"ObjectType version {version} published in Postgres, but SpiceDB "
                f"parent_project reconcile failed: {exc}"
            ),) from exc
    return result


@router.post("/ontology/{name}/reindex-search")
async def reindex_object_type_search_route(
    name: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """Rebuild OpenSearch documents for one ObjectType from the serving store."""
    await _authorize_ontology_governance(principal, workspace_id)
    try:
        object_type_urn = await core._object_type_urn_for(name, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HolonError.not_found('ObjectTypeNotFound', f"unknown ObjectType: {name}") from None
    opensearch_url = os.environ["HOLON_OPENSEARCH_URL"]
    opensearch_password = os.environ["HOLON_OPENSEARCH_PASSWORD"]
    try:
        return await catalog.reindex_object_type_search(
            core.pool,
            object_type_name=name,
            object_type_urn=object_type_urn,
            tenant_id=principal.tenant_id,
            opensearch_url=opensearch_url,
            opensearch_password=opensearch_password,
            allowed_countries=core.allowed_countries,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('ObjectTypeValidationFailed', str(exc)) from exc


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
        raise HolonError.not_found('ObjectTypeNotFound', f"unknown ObjectType: {name}")
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
        raise HolonError.invalid_argument('BranchValidationFailed', str(exc)) from exc


@router.get("/ontology/{name}/branches")
async def list_branches(
    name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> list[dict]:
    try:
        object_type_urn = await core._object_type_urn_for(name, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HolonError.not_found('ObjectTypeNotFound', f"unknown ObjectType: {name}")
    return await ontology.list_branches(core.pool, object_type_urn)


@router.get("/ontology/{name}/branches/{branch_name}")
async def get_branch(
    name: str, branch_name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    try:
        object_type_urn = await core._object_type_urn_for(name, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HolonError.not_found('ObjectTypeNotFound', f"unknown ObjectType: {name}")
    branch = await ontology.get_branch(core.pool, object_type_urn, branch_name)
    if branch is None:
        raise HolonError.not_found('BranchNotFound', f"unknown branch: {branch_name}")
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
        raise HolonError.not_found('ObjectTypeNotFound', f"unknown ObjectType: {name}")
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
        raise HolonError.invalid_argument('BranchValidationFailed', str(exc)) from exc


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
        raise HolonError.not_found('ObjectTypeNotFound', f"unknown ObjectType: {name}")
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
        raise HolonError.invalid_argument('BranchValidationFailed', str(exc)) from exc
    if request.decision == "approved":
        object_type = await ontology.get_object_type(core.pool, object_type_urn)
        try:
            await _link_object_type_to_project(object_type_urn, object_type.get("project_urn") if object_type else None)
        except Exception as exc:
            raise HolonError.unavailable('Unavailable', (
                    f"branch {branch_name!r} merged in Postgres, but SpiceDB "
                    f"parent_project reconcile failed: {exc}"
                ),) from exc
    return result


@router.get("/ontology/{name}/branches/{branch_name}/reviews")
async def list_branch_reviews(
    name: str, branch_name: str, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> list[dict]:
    try:
        object_type_urn = await core._object_type_urn_for(name, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HolonError.not_found('ObjectTypeNotFound', f"unknown ObjectType: {name}")
    branch = await ontology.get_branch(core.pool, object_type_urn, branch_name)
    if branch is None:
        raise HolonError.not_found('BranchNotFound', f"unknown branch: {branch_name}")
    return await ontology.list_branch_reviews(core.pool, branch["id"])
