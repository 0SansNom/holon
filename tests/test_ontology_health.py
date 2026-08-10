"""Unit tests for ontology health-check heuristics — no stack required.

`ontology_health` imports `core`/`ontology` at module load for the God
Object check; those need a live pool. Stub them so we can exercise the
pure Action Sprawl counter in isolation.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_APP = REPO_ROOT / "services" / "knowledge" / "app"

# Minimal package stubs so `from . import core, ontology` resolves.
_app = types.ModuleType("app")
_app.__path__ = [str(KNOWLEDGE_APP)]
sys.modules.setdefault("app", _app)
sys.modules.setdefault("app.core", types.ModuleType("app.core"))
sys.modules.setdefault("app.ontology", types.ModuleType("app.ontology"))
sys.modules.setdefault("holon_common", types.ModuleType("holon_common"))
sys.modules["holon_common"].Principal = object  # noqa: F401 — attribute for the import

# Prefer the knowledge app on sys.path so `import app.ontology_health` works.
sys.path.insert(0, str(KNOWLEDGE_APP.parent))

from app.ontology_health import _ACTION_SPRAWL_THRESHOLD, _check_action_sprawl  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def test_action_sprawl_attributes_interface_actions_to_implementing_ots() -> None:
    object_types = [
        {"name": "Supplier", "implements": ["Holdable"], "property_mapping": {"id": "id"}},
        {"name": "Customer", "implements": ["Holdable"], "property_mapping": {"id": "id"}},
    ]
    # Just over the threshold, all interface-scoped — previously bucketed under None.
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
