"""Wire helpers for Action preview / apply success and target resolution."""

from __future__ import annotations

from typing import Any, Optional

from holon_common import HolonError, build_urn


def validation_report(report: dict) -> dict:
    """Public preview / error validation payload (no internal Action Type row)."""
    return {
        "result": report["result"],
        "parameters": report.get("parameters") or {},
        "submissionCriteriaResult": report.get("submissionCriteriaResult"),
        "messages": report.get("messages") or [],
    }


def operation_id(tenant_id: str, workspace_id: str, apply_result: dict) -> Optional[str]:
    if apply_result.get("invocationId") is not None:
        return build_urn(tenant_id, workspace_id, "action-invocation", str(apply_result["invocationId"]))
    if apply_result.get("approvalId") is not None:
        return build_urn(tenant_id, workspace_id, "action-approval", str(apply_result["approvalId"]))
    return None


def resolve_target(
    action_type: dict,
    *,
    object_type: Optional[str],
    primary_key: Any,
) -> tuple[str, str]:
    """Resolve ObjectType + primary key from an explicit target.

    ``object_type`` may be omitted when the Action Type declares a single
    ``target_object_type``. Primary key is always required.
    """
    resolved_type = object_type or action_type.get("target_object_type")
    if not resolved_type or primary_key is None:
        raise HolonError.invalid_argument(
            "InvalidParameterCombination",
            "action target requires objectType (or Action Type target) and primaryKey",
        )
    return str(resolved_type), str(primary_key)


def success_envelope(
    *,
    tenant_id: str,
    workspace_id: str,
    object_type: str,
    primary_key: str,
    apply_result: dict,
) -> dict:
    body = {
        **apply_result,
        "target": {"objectType": object_type, "primaryKey": str(primary_key)},
    }
    op_id = operation_id(tenant_id, workspace_id, apply_result)
    if op_id:
        body["operationId"] = op_id
    return body
