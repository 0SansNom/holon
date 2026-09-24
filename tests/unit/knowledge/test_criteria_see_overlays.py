"""Unit tests: submission criteria must see Action overlays."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_DIR = REPO_ROOT / "services" / "knowledge"
LIBS = REPO_ROOT / "libs"


def _load():
    sys.path.insert(0, str(LIBS))
    sys.path.insert(0, str(KNOWLEDGE_DIR))
    app = types.ModuleType("app")
    app.__path__ = [str(KNOWLEDGE_DIR / "app")]
    sys.modules.setdefault("app", app)
    for name in ("asyncpg", "httpx", "pyiceberg", "pyiceberg.exceptions", "prometheus_client"):
        sys.modules.setdefault(name, MagicMock())

    holon = types.ModuleType("holon_common")
    holon.HolonError = type("HolonError", (Exception,), {})
    holon.Principal = object
    holon.build_urn = lambda *a, **k: "urn"
    holon.outbox = MagicMock()
    holon.EventActor = object
    holon.EventEnvelope = object
    sys.modules.setdefault("holon_common", holon)

    actions_pkg = types.ModuleType("app.actions")
    actions_pkg.__path__ = [str(KNOWLEDGE_DIR / "app" / "actions")]
    sys.modules["app.actions"] = actions_pkg
    hardcoded = types.ModuleType("app.actions.hardcoded")
    hardcoded._event = lambda **k: k
    sys.modules["app.actions.hardcoded"] = hardcoded
    metrics = types.ModuleType("app.actions.metrics")
    metrics.ACTION_EVENTS = MagicMock()
    metrics.ACTION_EVENTS.labels.return_value.inc = lambda: None
    sys.modules["app.actions.metrics"] = metrics

    from app.actions.declarative import (  # noqa: E402
        _apply_instance_edit_rows,
        _evaluate_criteria,
    )

    return _apply_instance_edit_rows, _evaluate_criteria


_apply_instance_edit_rows, _evaluate_criteria = _load()


def test_overlay_merge_makes_cancelled_visible_to_criteria() -> None:
    base = {"id": 5, "status": "pending"}
    edits = [{"property_name": "cancelled", "property_value": json.dumps(True)}]
    row = _apply_instance_edit_rows(base, edits)
    assert row["cancelled"] is True

    criteria = [
        {
            "all": [
                {"property": "status", "operator": "eq", "value": "pending"},
                {"property": "cancelled", "operator": "neq", "value": True, "message": "already cancelled"},
            ],
            "message": "order is not cancellable",
        }
    ]
    # Without overlay → would pass; with overlay → refuse.
    assert _evaluate_criteria({"id": 5, "status": "pending"}, criteria) is None
    err = _evaluate_criteria(row, criteria)
    assert err == "order is not cancellable"


def test_closed_account_overlay_blocks_credit_hold() -> None:
    row = _apply_instance_edit_rows(
        {"id": 4, "account_closed": False},
        [{"property_name": "account_closed", "property_value": True}],
    )
    assert row["account_closed"] is True
    err = _evaluate_criteria(
        row,
        [
            {
                "property": "account_closed",
                "operator": "neq",
                "value": True,
                "message": "cannot put a closed account on credit hold",
            }
        ],
    )
    assert err == "cannot put a closed account on credit hold"
