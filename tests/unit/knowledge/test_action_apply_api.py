"""Unit tests for Holon Action wire helpers (no stack)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
KNOWLEDGE_DIR = REPO / "services" / "knowledge"
LIBS = REPO / "libs"


def _import_helpers():
    sys.path.insert(0, str(LIBS))
    sys.path.insert(0, str(KNOWLEDGE_DIR))
    app = types.ModuleType("app")
    app.__path__ = [str(KNOWLEDGE_DIR / "app")]
    sys.modules.setdefault("app", app)
    actions_pkg = types.ModuleType("app.actions")
    actions_pkg.__path__ = [str(KNOWLEDGE_DIR / "app" / "actions")]
    sys.modules["app.actions"] = actions_pkg
    from app.actions.wire import (  # noqa: E402
        operation_id,
        resolve_target,
        success_envelope,
        validation_report,
    )
    from holon_common import HolonError  # noqa: E402

    return operation_id, resolve_target, success_envelope, validation_report, HolonError


operation_id, resolve_target, success_envelope, validation_report, HolonError = _import_helpers()


def test_resolve_target_uses_action_type_and_primary_key() -> None:
    action_type = {"target_object_type": "Customer"}
    ot, pk = resolve_target(action_type, object_type=None, primary_key=42)
    assert ot == "Customer"
    assert pk == "42"


def test_resolve_target_requires_key() -> None:
    action_type = {"target_object_type": "Customer"}
    with pytest.raises(HolonError) as exc:
        resolve_target(action_type, object_type=None, primary_key=None)
    assert exc.value.error_name == "InvalidParameterCombination"


def test_validation_report_shape() -> None:
    body = validation_report(
        {
            "result": "INVALID",
            "parameters": {"x": {"result": "INVALID", "required": True, "evaluatedConstraints": []}},
            "submissionCriteriaResult": "VALID",
            "messages": ["bad"],
        }
    )
    assert body["result"] == "INVALID"
    assert body["messages"] == ["bad"]
    assert body["parameters"]["x"]["result"] == "INVALID"


def test_success_envelope_adds_target_and_operation_id() -> None:
    body = success_envelope(
        tenant_id="t",
        workspace_id="main",
        object_type="Customer",
        primary_key="1",
        apply_result={"status": "applied", "action": "Customer.x", "invocationId": 9},
    )
    assert body["target"] == {"objectType": "Customer", "primaryKey": "1"}
    assert body["operationId"] == "hl:t:main:action-invocation:9"
    assert operation_id("t", "main", {"approvalId": 3}) == "hl:t:main:action-approval:3"
