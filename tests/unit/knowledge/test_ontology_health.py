"""Tests for Ontology Health."""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_APP = REPO_ROOT / "services" / "knowledge" / "app"

# Minimal package stubs so `from . import core, ontology` resolves.
_app = types.ModuleType("app")
_app.__path__ = [str(KNOWLEDGE_APP)]
sys.modules.setdefault("app", _app)
sys.modules.setdefault("app.core", types.ModuleType("app.core"))
sys.modules.setdefault("app.ontology", types.ModuleType("app.ontology"))
# Stub holon_common only when absent — never mutate a real loaded package
# (``Principal = object`` on the live module poisoned other suites at collection).
if "holon_common" not in sys.modules:
    _hc = types.ModuleType("holon_common")
    _hc.Principal = object
    sys.modules["holon_common"] = _hc

# Prefer the knowledge app on sys.path so `import app.ontology_health` works.
sys.path.insert(0, str(KNOWLEDGE_APP.parent))

from app.ontology_health import (  # noqa: E402
    _ACTION_SPRAWL_THRESHOLD,
    _check_action_sprawl,
    _check_metadata_gaps,
)


def _run(coro):
    return asyncio.run(coro)


def test_metadata_gaps_flags_missing_pk_title_and_mn_without_join() -> None:
    link_overlays = types.ModuleType("app.link_overlays")

    async def count_overlays(*_args, **_kwargs):
        return 0

    link_overlays.count_overlays = count_overlays
    sys.modules["app.link_overlays"] = link_overlays
    sys.modules["app.core"].pool = object()

    findings = _run(
        _check_metadata_gaps(
            [
                {"name": "Broken", "property_mapping": {"name": "name"}, "primary_key": "id"},
                {"name": "Ok", "property_mapping": {"id": "id", "name": "name"}, "primary_key": "id", "title_key": "name"},
            ],
            [
                {
                    "name": "A.b",
                    "urn": "hl:acme:global:relation-type:A.b",
                    "cardinality": "many_to_many",
                    "storage_kind": "foreign_key",
                },
                {
                    "name": "C.d",
                    "urn": "hl:acme:global:relation-type:C.d",
                    "cardinality": "many_to_many",
                    "storage_kind": "join_dataset",
                    "join_dataset_urn": None,
                },
            ],
            tenant_id="acme",
        )
    )
    kinds = {(f["kind"], f["object_type"]) for f in findings}
    assert ("missing_primary_key", "Broken") in kinds
    assert ("missing_title_key", "Broken") in kinds
    assert ("mn_without_join", "A.b") in kinds
    assert ("join_dataset_incomplete", "C.d") in kinds
    assert ("missing_primary_key", "Ok") not in kinds
    assert ("missing_title_key", "Ok") not in kinds


def test_action_sprawl_attributes_interface_actions_to_implementing_ots() -> None:
    object_types = [
        {"name": "Supplier", "implements": ["Holdable"], "property_mapping": {"id": "id"}},
        {"name": "Customer", "implements": ["Holdable"], "property_mapping": {"id": "id"}},
    ]
    # Just over the threshold, all interface-scoped
    action_types = [
        {"name": f"Holdable.a{i}", "target_object_type": None, "target_interface": "Holdable"}
        for i in range(_ACTION_SPRAWL_THRESHOLD + 1)
    ]

    findings = _run(_check_action_sprawl(object_types, action_types))
    by_ot = {f["object_type"]: f for f in findings}
    assert "Supplier" in by_ot, findings
    assert "Customer" in by_ot, findings
    assert None not in by_ot and "None" not in by_ot, findings
    assert by_ot["Supplier"]["kind"] == "action_sprawl"
    assert str(_ACTION_SPRAWL_THRESHOLD + 1) in by_ot["Supplier"]["detail"]


def test_action_sprawl_orphaned_interface_actions_use_synthetic_key() -> None:
    object_types = [{"name": "Supplier", "implements": [], "property_mapping": {"id": "id"}}]
    action_types = [
        {"name": f"Orphan.a{i}", "target_object_type": None, "target_interface": "Orphan"}
        for i in range(_ACTION_SPRAWL_THRESHOLD + 1)
    ]

    findings = _run(_check_action_sprawl(object_types, action_types))
    assert len(findings) == 1, findings
    assert findings[0]["object_type"] == "interface:Orphan", findings
    assert "Orphan" in findings[0]["detail"]


def test_action_sprawl_ot_targeted_unchanged() -> None:
    object_types = [{"name": "Supplier", "implements": [], "property_mapping": {"id": "id"}}]
    action_types = [
        {"name": f"Supplier.a{i}", "target_object_type": "Supplier", "target_interface": None}
        for i in range(_ACTION_SPRAWL_THRESHOLD + 1)
    ]

    findings = _run(_check_action_sprawl(object_types, action_types))
    assert len(findings) == 1
    assert findings[0]["object_type"] == "Supplier"
