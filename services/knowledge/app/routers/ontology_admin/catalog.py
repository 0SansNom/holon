"""Catalog datasets, preview/stats, and join-dataset generation."""

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


@router.get("/catalog/datasets")
async def get_datasets(
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> list[dict]:
    await _authorize_workspace_read(principal, workspace_id)
    return await catalog.list_datasets(core.pool, principal.tenant_id)


@router.get("/catalog/datasets/{name}/preview")
async def preview_dataset(
    name: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """One sample row's column names — what a "Create Object Type" form
    suggests a property mapping from, instead of asking a non-technical
    admin to type raw JSON keys from memory. Auth-only, like every other
    definition/metadata read in this router: a column *name* isn't row
    data the PDP has anything to enforce on, the sample value shown is
    illustrative only (never asserted to be safe to persist anywhere).
    """
    await _authorize_workspace_read(principal, workspace_id)
    try:
        rows = await asyncio.to_thread(resolver.fetch_generic, name, **core.iceberg_kwargs(principal.tenant_id))
    except (NoSuchTableError, ValueError):
        # ValueError: `name` isn't even a legal Iceberg identifier
        # (holon_common.iceberg_ident) — such a dataset could never
        # have been synced, same "not found" outcome as NoSuchTableError.
        raise HolonError.not_found('DatasetNotFound', f"dataset {name!r} has never been synced")
    if not rows:
        return {"columns": []}
    return {"columns": [{"name": key, "sample": value} for key, value in rows[0].items()]}


@router.get("/catalog/datasets/{name}/versions")
async def get_dataset_versions(name: str, principal: Principal = Depends(core.current_principal)) -> list[dict]:
    """Full snapshot history — every sync/pipeline-run that ever
    produced a version of this dataset, newest first. The data has
    always been recorded (`catalog._catalogue_sync` inserts one
    immutable `dataset_version` row per snapshot); this just exposes it.
    """
    dataset_urn = build_urn(principal.tenant_id, core.WORKSPACE_ID, "dataset", name)
    return await catalog.list_dataset_versions(core.pool, principal.tenant_id, dataset_urn)


@router.get("/catalog/datasets/{name}/stats")
async def get_dataset_stats(
    name: str,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """The Iceberg table's real declared schema plus per-column stats
    (null/distinct counts, min/max) — heavier than `/preview` (a full
    scan + DuckDB aggregation, not one sample row), so its own endpoint.
    """
    await _authorize_workspace_read(principal, workspace_id)
    try:
        return await asyncio.to_thread(
            resolver.dataset_schema_and_stats, name, **core.iceberg_kwargs(principal.tenant_id)
        )
    except (NoSuchTableError, ValueError):
        # ValueError: `name` isn't even a legal Iceberg identifier
        # (holon_common.iceberg_ident) — such a dataset could never
        # have been synced, same "not found" outcome as NoSuchTableError.
        raise HolonError.not_found('DatasetNotFound', f"dataset {name!r} has never been synced")


class GenerateJoinDatasetRequest(BaseModel):
    name: str
    source_column: str
    target_column: str


@router.post("/catalog/join-datasets", status_code=201)
async def generate_join_dataset(
    request: GenerateJoinDatasetRequest,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """Foundry-style "Generate join table" — empty 2-column Iceberg bridge.

    Publishes a `connectivity.sync.completed` event, same as every other
    dataset sync — cataloguing happens in Knowledge's own bus consumer
    (`catalog.consume_events`), not via a direct Postgres write, so the
    projection stays reconstructible from the bus alone (no connector
    actually ran this sync, so `connector_urn` names this feature itself
    as the producing "connector"). Requires workspace `approve`.
    """
    from ... import join_datasets

    await _authorize_ontology_governance(principal, workspace_id)
    try:
        result = await asyncio.to_thread(
            join_datasets.create_empty_join_table,
            request.name,
            tenant_id=principal.tenant_id,
            source_column=request.source_column,
            target_column=request.target_column,
            **core.ICEBERG_CONFIG,
        )
    except ValueError as exc:
        raise HolonError.invalid_argument('DatasetValidationFailed', str(exc)) from exc
    payload = join_datasets.catalog_payload(
        tenant_id=principal.tenant_id, workspace_id=workspace_id, result=result
    )
    connector_urn = build_urn(principal.tenant_id, workspace_id, "connector", "join-dataset-generator")
    event_id = uuid.uuid4().hex
    await core.producer.publish(
        EventEnvelope(
            event_id=event_id,
            event_type="connectivity.sync.completed",
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            aggregate_type="Connector",
            aggregate_id=connector_urn,
            correlation_id=event_id,
            partition_key=f"{principal.tenant_id}/{payload['dataset_urn']}",
            producer="knowledge-platform@0.1.0",
            actor=EventActor(type=principal.type, urn=principal.urn, on_behalf_of=principal.on_behalf_of),
            payload={**payload, "connector_urn": connector_urn, "source_dataset_version_urn": None},
        )
    )
    return {
        **payload,
        "source_column": result.source_column,
        "target_column": result.target_column,
        "iceberg_namespace": result.namespace,
        "iceberg_table": result.table,
    }
