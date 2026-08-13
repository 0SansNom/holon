"""Unit tests for interface property_types helpers (no HTTP stack)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INTERFACES_PATH = REPO_ROOT / "services" / "knowledge" / "app" / "ontology" / "interfaces.py"
LIFECYCLE_PATH = REPO_ROOT / "services" / "knowledge" / "app" / "ontology" / "lifecycle.py"
LIBS = REPO_ROOT / "libs"


def _load_interfaces_module():
    """Load interfaces.py without pulling ontology.__init__ (httpx, etc.)."""
    sys.path.insert(0, str(LIBS))

    # Minimal package shells so relative imports resolve.
    app = types.ModuleType("app")
    app.__path__ = [str(REPO_ROOT / "services" / "knowledge" / "app")]
    sys.modules.setdefault("app", app)
    ontology_pkg = types.ModuleType("app.ontology")
    ontology_pkg.__path__ = [str(REPO_ROOT / "services" / "knowledge" / "app" / "ontology")]
    sys.modules.setdefault("app.ontology", ontology_pkg)

    lifecycle_spec = importlib.util.spec_from_file_location("app.ontology.lifecycle", LIFECYCLE_PATH)
    assert lifecycle_spec and lifecycle_spec.loader
    lifecycle_mod = importlib.util.module_from_spec(lifecycle_spec)
    sys.modules["app.ontology.lifecycle"] = lifecycle_mod
    lifecycle_spec.loader.exec_module(lifecycle_mod)

    # Stub asyncpg for the type annotation import.
    sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

    spec = importlib.util.spec_from_file_location("app.ontology.interfaces", INTERFACES_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["app.ontology.interfaces"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_property_type_binding_key_leaves() -> None:
    mod = _load_interfaces_module()
    key = mod.property_type_binding_key
    assert key({"kind": "value_type", "value_type": "Email"}) == ("value_type", "Email")
    assert key({"kind": "shared_property_type", "shared_property_type": "email"}) == (
        "shared_property_type",
        "email",
    )
    assert key({"kind": "struct", "properties": {}}) is None
    assert key({"kind": "value_type"}) is None


def test_property_types_tighten_detects_add_and_change() -> None:
    mod = _load_interfaces_module()
    tighten = mod.property_types_tighten
    prev = {"country": {"kind": "value_type", "value_type": "A"}}
    assert not tighten(prev, prev)
    assert not tighten(prev, {})
    assert tighten({}, prev)
    assert tighten(
        prev,
        {"country": {"kind": "value_type", "value_type": "B"}},
    )
    assert tighten(
        prev,
        {"country": {"kind": "shared_property_type", "shared_property_type": "country"}},
    )
