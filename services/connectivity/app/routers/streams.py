"""Connectivity streams routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from holon_common import HolonError, Principal, build_urn
from holon_common.audit import emit_audit

from .. import deps, kafka_stream_registry
from ..deps import _authorize_workspace, current_principal
from ..ingest import (
    RegisterKafkaStreamRequest,
    _cancel_kafka_stream_task,
    _kafka_stream_not_found,
    _spawn_kafka_stream_task,
)


router = APIRouter()


@router.post("/kafka-streams", status_code=201)
async def register_kafka_stream(body: RegisterKafkaStreamRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Register a new Kafka stream source."""
    await _authorize_workspace(principal, "write")
    if body.batch_interval_seconds <= 0:
        raise HolonError.invalid_argument("InvalidBatchInterval", "batch_interval_seconds must be positive")
    try:
        source = await kafka_stream_registry.register_source(
            deps.pool,
            tenant_id=principal.tenant_id,
            name=body.name,
            topic=body.topic,
            key_field=body.key_field,
            dataset_name=body.dataset_name,
            batch_interval_seconds=body.batch_interval_seconds,
            created_by_urn=principal.urn,
        )
    except kafka_stream_registry.KafkaStreamConflictError as exc:
        raise HolonError.conflict('KafkaStreamConflict', str(exc)) from exc
    emit_audit(
        category="access",
        action="connectivity.kafka_stream.registered",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="kafka_stream",
        resource_urn=build_urn(principal.tenant_id, "global", "kafka-stream", body.name),
        extra={"topic": body.topic, "dataset_name": body.dataset_name},
    )
    _spawn_kafka_stream_task(source)
    return source



@router.get("/kafka-streams")
async def list_kafka_streams(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await kafka_stream_registry.list_sources(deps.pool, principal.tenant_id)



@router.get("/kafka-streams/{name}")
async def get_kafka_stream(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "read")
    source = await kafka_stream_registry.get_source(deps.pool, principal.tenant_id, name)
    if source is None:
        raise _kafka_stream_not_found(name)
    return source



@router.post("/kafka-streams/{name}/disable")
async def disable_kafka_stream(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    source = await kafka_stream_registry.get_source(deps.pool, principal.tenant_id, name)
    if source is None:
        raise _kafka_stream_not_found(name)
    result = await kafka_stream_registry.set_status(deps.pool, principal.tenant_id, name, "disabled")
    _cancel_kafka_stream_task(principal.tenant_id, name)
    return result



@router.post("/kafka-streams/{name}/enable")
async def enable_kafka_stream(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    source = await kafka_stream_registry.get_source(deps.pool, principal.tenant_id, name)
    if source is None:
        raise _kafka_stream_not_found(name)
    result = await kafka_stream_registry.set_status(deps.pool, principal.tenant_id, name, "active")
    _spawn_kafka_stream_task(result)
    return result



@router.delete("/kafka-streams/{name}")
async def delete_kafka_stream(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    deleted = await kafka_stream_registry.delete_source(deps.pool, principal.tenant_id, name)
    if not deleted:
        raise _kafka_stream_not_found(name)
    _cancel_kafka_stream_task(principal.tenant_id, name)
    return {"deleted": name}

