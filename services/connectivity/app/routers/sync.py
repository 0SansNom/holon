"""Connectivity sync routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, Query

from holon_common import EventActor, HolonError, Principal

from .. import (
    deps,
    generic_source_registry,
    object_source_registry,
    salesforce_source_registry,
    sql_source_registry,
)
from ..deps import (
    SyncRequest,
    SyncResult,
    _authorize_source,
    _authorize_workspace,
    _resolve_workspace,
    current_principal,
)
from ..ingest import _run_sync_for_dataset


router = APIRouter()


async def _resolve_source_for_sync(tenant_id: str, dataset: str) -> Optional[dict]:
    for registry in (
        generic_source_registry,
        sql_source_registry,
        object_source_registry,
        salesforce_source_registry,
    ):
        source = await registry.get_source(deps.pool, tenant_id, dataset)
        if source is not None:
            return source
    return None


@router.post("/sync", response_model=SyncResult)
async def run_sync(
    request: SyncRequest = SyncRequest(),
    principal: Principal = Depends(current_principal),
    workspace_id: Optional[str] = Query(None, alias="workspaceId"),
    x_holon_workspace_id: Optional[str] = Header(None, alias="X-Holon-Workspace-Id"),
) -> SyncResult:
    target_workspace = _resolve_workspace(
        explicit=request.workspace_id,
        workspace_id=workspace_id,
        x_holon_workspace_id=x_holon_workspace_id,
    )
    source = await _resolve_source_for_sync(principal.tenant_id, request.dataset)
    if source is not None and source.get("workspace_id"):
        stored = source["workspace_id"]
        if request.workspace_id and request.workspace_id != stored:
            raise HolonError.invalid_argument(
                "SourceWorkspaceMismatch",
                (
                    f"source {request.dataset!r} is bound to workspace {stored!r}; "
                    f"got {request.workspace_id!r}"
                ),
                dataset=request.dataset,
                expected_workspace_id=stored,
                got_workspace_id=request.workspace_id,
            )
        target_workspace = stored
        await _authorize_source(
            principal, "write", name=request.dataset, workspace_id=target_workspace
        )
    else:
        # Plugin-backed datasets (no source row) stay at workspace grain.
        await _authorize_workspace(principal, "write", workspace_id=target_workspace)
    actor = EventActor(type=principal.type, urn=principal.urn, on_behalf_of=principal.on_behalf_of)
    return await _run_sync_for_dataset(
        request.dataset, actor=actor, tenant_id=principal.tenant_id, workspace_id=target_workspace
    )


@router.get("/syncs")
async def list_syncs(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    rows = await deps.pool.fetch(
        "SELECT * FROM sync_run WHERE tenant_id = $1 ORDER BY id DESC", principal.tenant_id
    )
    return [dict(row) for row in rows]
