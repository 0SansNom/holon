"""Connectivity writebacks routes."""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends

from holon_common import HolonError, Principal, build_urn
from holon_common.audit import emit_audit

from .. import deps, write_target_registry
from ..deps import (
    CLOSE_ACCOUNT_FAILURE_SENTINEL,
    _authorize_workspace,
    _source_db_url,
    current_principal,
)
from ..ingest import (
    CloseAccountRequest,
    RegisterWriteTargetRequest,
    WriteSourceRequest,
    _require_workflow_engine,
)


router = APIRouter()


@router.post("/source/customers/{customer_id}/close-account")
async def close_source_customer_account(
    customer_id: int, request: CloseAccountRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """`reason == CLOSE_ACCOUNT_FAILURE_SENTINEL` is a documented test hook
    for exercising the saga's compensation path deterministically — not a
    real failure mode.
    """
    _require_workflow_engine(principal)
    await _authorize_workspace(principal, "write")
    if request.reason == CLOSE_ACCOUNT_FAILURE_SENTINEL:
        raise HolonError.internal('InternalError', "simulated downstream failure")

    conn = await asyncpg.connect(_source_db_url())
    try:
        row = await conn.fetchrow(
            "UPDATE customers SET account_closed = true WHERE id = $1 RETURNING id, account_closed",
            customer_id,
        )
    finally:
        await conn.close()

    if row is None:
        raise HolonError.not_found('SourceCustomerNotFound', f"customer {customer_id} not found in source_erp", customer_id=customer_id)
    return dict(row)



@router.get("/source/customers/{customer_id}")
async def get_source_customer(customer_id: int, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "read")
    conn = await asyncpg.connect(_source_db_url())
    try:
        row = await conn.fetchrow("SELECT id, account_closed FROM customers WHERE id = $1", customer_id)
    finally:
        await conn.close()

    if row is None:
        raise HolonError.not_found('SourceCustomerNotFound', f"customer {customer_id} not found in source_erp", customer_id=customer_id)
    return dict(row)



@router.post("/write-targets", status_code=201)
async def register_write_target(
    request: RegisterWriteTargetRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Register a writeback target schema for declarative actions."""
    await _authorize_workspace(principal, "write")
    try:
        return await write_target_registry.register_write_target(
            deps.pool,
            tenant_id=principal.tenant_id,
            dataset_name=request.dataset_name,
            table_name=request.table_name,
            id_column=request.id_column,
            allowed_properties=request.allowed_properties,
            created_by_urn=principal.urn,
        )
    except write_target_registry.WriteTargetConfigError as exc:
        raise HolonError.invalid_argument('PluginValidationFailed', str(exc)) from exc



@router.get("/write-targets")
async def list_write_targets(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await write_target_registry.list_write_targets(deps.pool, principal.tenant_id)



@router.get("/write-targets/{dataset_name}")
async def get_write_target(dataset_name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "read")
    target = await write_target_registry.get_write_target(deps.pool, principal.tenant_id, dataset_name)
    if target is None:
        raise HolonError.not_found('DatasetNotFound', f"no write target registered for dataset {dataset_name!r}")
    return target



@router.delete("/write-targets/{dataset_name}")
async def delete_write_target(dataset_name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    if await write_target_registry.get_write_target(deps.pool, principal.tenant_id, dataset_name) is None:
        raise HolonError.not_found('DatasetNotFound', f"no write target registered for dataset {dataset_name!r}")
    await write_target_registry.delete_write_target(deps.pool, principal.tenant_id, dataset_name)
    emit_audit(
        category="access",
        action="connectivity.write_target.deleted",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="write_target",
        resource_urn=build_urn(principal.tenant_id, "global", "write-target", dataset_name),
    )
    return {"deleted": dataset_name}



@router.post("/source/{dataset_name}/{instance_id}/write")
async def write_source(
    dataset_name: str, instance_id: str, request: WriteSourceRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Apply writeback edits to a target dataset instance."""
    _require_workflow_engine(principal)
    await _authorize_workspace(principal, "write")
    try:
        return await write_target_registry.apply_write(
            deps.pool, _source_db_url(),
            tenant_id=principal.tenant_id, dataset_name=dataset_name, instance_id=instance_id, edits=request.edits,
        )
    except write_target_registry.UnknownWriteTargetError as exc:
        raise HolonError.not_found("WriteTargetNotFound", str(exc)) from exc
    except write_target_registry.InstanceNotFoundError as exc:
        raise HolonError.not_found("WriteTargetInstanceNotFound", str(exc)) from exc
    except write_target_registry.WriteTargetConfigError as exc:
        raise HolonError.invalid_argument('SourceValidationFailed', str(exc)) from exc

