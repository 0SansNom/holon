"""Unit tests for P2b submission criteria validation + evaluation."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_DIR = REPO_ROOT / "services" / "knowledge"
LIBS = REPO_ROOT / "libs"


def _setup():
    sys.path.insert(0, str(LIBS))
    sys.path.insert(0, str(KNOWLEDGE_DIR))
    app = types.ModuleType("app")
    app.__path__ = [str(KNOWLEDGE_DIR / "app")]
    sys.modules.setdefault("app", app)
    ontology_pkg = types.ModuleType("app.ontology")
    ontology_pkg.__path__ = [str(KNOWLEDGE_DIR / "app" / "ontology")]
    sys.modules["app.ontology"] = ontology_pkg
    tc = types.ModuleType("app.ontology.type_classes")
    tc.normalize_type_classes = lambda x: list(x or [])
    sys.modules["app.ontology.type_classes"] = tc
    life = types.ModuleType("app.ontology.lifecycle")
    life.normalize_deprecation_metadata = lambda *a, **k: {
        "lifecycle_status": "experimental",
        "deprecation_reason": None,
        "deprecation_deadline": None,
        "replacement_urn": None,
    }
    sys.modules["app.ontology.lifecycle"] = life
    structural = types.ModuleType("app.action_structural")
    structural.validate_edit_declaration = lambda *a, **k: None
    structural.is_property_edit = lambda e: (e.get("kind") or "modify_property") == "modify_property"
    sys.modules["app.action_structural"] = structural

    from app.ontology.action_types import validate_submission_criterion  # noqa: E402

    # declarative criteria eval without full package
    actions_pkg = types.ModuleType("app.actions")
    actions_pkg.__path__ = [str(KNOWLEDGE_DIR / "app" / "actions")]
    sys.modules["app.actions"] = actions_pkg
    hardcoded = types.ModuleType("app.actions.hardcoded")
    hardcoded._event = lambda **k: k
    sys.modules["app.actions.hardcoded"] = hardcoded

    from app.actions.declarative import _evaluate_criteria  # noqa: E402
    from holon_common.auth import Principal  # noqa: E402

    return validate_submission_criterion, _evaluate_criteria, Principal


validate_submission_criterion, _evaluate_criteria, Principal = _setup()


def test_validate_all_any_and_message() -> None:
    validate_submission_criterion(
        {
            "any": [
                {"property": "status", "operator": "eq", "value": "open"},
                {"property": "status", "operator": "eq", "value": "pending"},
            ],
            "message": "must be open or pending",
        }
    )
    validate_submission_criterion(
        {"principal": "type", "operator": "eq", "value": "user", "message": "humans only"}
    )


def test_evaluate_any_and_principal() -> None:
    principal = Principal(
        urn="hl:acme:user:jdoe",
        type="user",
        tenant_id="acme",
        display_name="Jane",
    )
    assert (
        _evaluate_criteria(
            {"status": "closed"},
            [
                {
                    "any": [
                        {"property": "status", "operator": "eq", "value": "open"},
                        {"property": "status", "operator": "eq", "value": "closed"},
                    ]
                }
            ],
            principal=principal,
        )
        is None
    )
    err = _evaluate_criteria(
        {"status": "open"},
        [{"principal": "type", "operator": "eq", "value": "agent", "message": "agents only"}],
        principal=principal,
    )
    assert err == "agents only"


def test_reject_bad_group() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        validate_submission_criterion({"all": [], "any": []})
