"""Public Ontology list and Holon Action preview / apply / batch endpoints."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from holon_common import HolonError, Principal

from .. import actions, core, ontology
from ..actions import ActionValidationError
from ..actions.wire import resolve_target, success_envelope, validation_report

router = APIRouter()

APPLY_BATCH_MAX = 50


@router.get("/api/ontologies")
async def list_ontologies(principal: Principal = Depends(core.current_principal)) -> dict:
    """List ontologies visible to the caller (one apiName per workspace)."""
    workspace_id = core.WORKSPACE_ID
    return {
        "data": [
            {
                "apiName": workspace_id,
                "displayName": workspace_id,
                "rid": f"hl:{principal.tenant_id}:{workspace_id}:ontology:{workspace_id}",
            }
        ],
        "nextPageToken": None,
    }


@router.get("/api/ontologies/{ontology}")
async def get_ontology(ontology: str, principal: Principal = Depends(core.current_principal)) -> dict:
    workspace_id = core.WORKSPACE_ID
    if ontology not in {workspace_id, "main"}:
        raise HolonError.not_found("OntologyNotFound", f"unknown ontology: {ontology}", ontology=ontology)
    return {
        "apiName": workspace_id,
        "displayName": workspace_id,
        "rid": f"hl:{principal.tenant_id}:{workspace_id}:ontology:{workspace_id}",
    }


class ActionTarget(BaseModel):
    objectType: Optional[str] = None
    primaryKey: Any


class ActionApplyRequest(BaseModel):
    target: ActionTarget
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    ttl_seconds: Optional[int] = None


class ActionPreviewRequest(BaseModel):
    target: ActionTarget
    parameters: dict[str, Any] = Field(default_factory=dict)


class ActionBatchTarget(BaseModel):
    objectType: Optional[str] = None
    primaryKey: Any


class ActionBatchRequest(BaseModel):
    targets: list[ActionBatchTarget] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    ttl_seconds: Optional[int] = None


async def _resolve_action_type(tenant_id: str, action_ref: str, object_type_hint: Optional[str]) -> dict:
    row = await ontology.get_action_type(core.pool, tenant_id, action_ref)
    if row is not None:
        return row
    if object_type_hint and "." not in action_ref:
        qualified = f"{object_type_hint}.{action_ref}"
        row = await ontology.get_action_type(core.pool, tenant_id, qualified)
        if row is not None:
            return row
    raise HolonError.not_found("ActionTypeNotFound", f"unknown Action Type: {action_ref}", action_name=action_ref)


async def _authorize_and_prepare(
    *,
    action_ref: str,
    object_type: Optional[str],
    primary_key: Any,
    principal: Principal,
    workspace_id: str,
) -> tuple[str, str, str]:
    action_type = await _resolve_action_type(principal.tenant_id, action_ref, object_type)
    resolved_type, resolved_key = resolve_target(
        action_type,
        object_type=object_type,
        primary_key=primary_key,
    )
    try:
        object_type_urn = await core._object_type_urn_for(
            resolved_type, tenant_id=principal.tenant_id, workspace_id=workspace_id
        )
    except KeyError:
        raise HolonError.not_found(
            "ObjectTypeNotFound", f"unknown ObjectType: {resolved_type}", object_type=resolved_type
        )
    await core._authorize_object_type(principal, object_type_urn, action_type["required_permission"])
    return action_type["name"], resolved_type, resolved_key


@router.post("/api/ontologies/{ontology}/actions/{action}/preview")
async def preview_action(
    ontology: str,
    action: str,
    request: ActionPreviewRequest,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """Dry-run validation for one Action application (no writes)."""
    _ = ontology
    try:
        action_name, object_type, instance_id = await _authorize_and_prepare(
            action_ref=action,
            object_type=request.target.objectType,
            primary_key=request.target.primaryKey,
            principal=principal,
            workspace_id=workspace_id,
        )
        report = await actions.validate_generic_action(
            core.pool,
            action_name=action_name,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            object_type=object_type,
            instance_id=instance_id,
            principal=principal,
            parameters=request.parameters,
        )
    except LookupError as exc:
        raise HolonError.not_found("ActionNotFound", str(exc)) from exc
    body = validation_report(report)
    body["target"] = {"objectType": object_type, "primaryKey": instance_id}
    return body


@router.post("/api/ontologies/{ontology}/actions/{action}")
async def apply_action(
    ontology: str,
    action: str,
    request: ActionApplyRequest,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """Apply one Action. Invalid input → 400 ActionValidationFailed."""
    _ = ontology
    try:
        action_name, object_type, instance_id = await _authorize_and_prepare(
            action_ref=action,
            object_type=request.target.objectType,
            primary_key=request.target.primaryKey,
            principal=principal,
            workspace_id=workspace_id,
        )
        apply_result = await actions.request_generic_action(
            core.pool,
            action_name=action_name,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            object_type=object_type,
            instance_id=instance_id,
            principal=principal,
            reason=request.reason or "api apply",
            parameters=request.parameters,
            ttl_seconds=request.ttl_seconds,
        )
    except ActionValidationError as exc:
        raise exc.to_holon_error() from exc
    except LookupError as exc:
        raise HolonError.not_found("ActionNotFound", str(exc)) from exc
    return success_envelope(
        tenant_id=principal.tenant_id,
        workspace_id=workspace_id,
        object_type=object_type,
        primary_key=instance_id,
        apply_result=apply_result,
    )


@router.post("/api/ontologies/{ontology}/actions/{action}/batch")
async def apply_action_batch(
    ontology: str,
    action: str,
    request: ActionBatchRequest,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """Apply the same Action + parameters to multiple explicit targets (max 50)."""
    _ = ontology
    if not request.targets:
        raise HolonError.invalid_argument("EmptyBatch", "targets must be non-empty")
    if len(request.targets) > APPLY_BATCH_MAX:
        raise HolonError.invalid_argument(
            "BatchTooLarge", f"targets capped at {APPLY_BATCH_MAX} per batch", limit=APPLY_BATCH_MAX
        )

    results: list[dict] = []
    for item in request.targets:
        target_key = str(item.primaryKey) if item.primaryKey is not None else ""
        try:
            action_name, object_type, instance_id = await _authorize_and_prepare(
                action_ref=action,
                object_type=item.objectType,
                primary_key=item.primaryKey,
                principal=principal,
                workspace_id=workspace_id,
            )
            apply_result = await actions.request_generic_action(
                core.pool,
                action_name=action_name,
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                object_type=object_type,
                instance_id=instance_id,
                principal=principal,
                reason=request.reason or "api batch",
                parameters=request.parameters,
                ttl_seconds=request.ttl_seconds,
            )
            results.append(
                {
                    "ok": True,
                    "result": success_envelope(
                        tenant_id=principal.tenant_id,
                        workspace_id=workspace_id,
                        object_type=object_type,
                        primary_key=instance_id,
                        apply_result=apply_result,
                    ),
                }
            )
        except ActionValidationError as exc:
            results.append(
                {
                    "ok": False,
                    "target": {"objectType": item.objectType, "primaryKey": target_key},
                    "error": exc.to_holon_error().to_body(service="knowledge-platform"),
                }
            )
        except HolonError as exc:
            results.append(
                {
                    "ok": False,
                    "target": {"objectType": item.objectType, "primaryKey": target_key},
                    "error": exc.to_body(service="knowledge-platform"),
                }
            )
        except LookupError as exc:
            results.append(
                {
                    "ok": False,
                    "target": {"objectType": item.objectType, "primaryKey": target_key},
                    "error": HolonError.not_found("ActionNotFound", str(exc)).to_body(service="knowledge-platform"),
                }
            )

    return {
        "action": action,
        "count": len(results),
        "succeeded": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "results": results,
    }
