"""Current-state lineage graph and action property diffs. No live stack."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

LIBS = Path(__file__).resolve().parents[3] / "libs"
KNOWLEDGE_DIR = Path(__file__).resolve().parents[3] / "services" / "knowledge"
sys.path.insert(0, str(LIBS))
sys.path.insert(0, str(KNOWLEDGE_DIR))

from app.action_structural import property_changes  # noqa: E402
from app.lineage import assemble_graph  # noqa: E402

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 1, 2, tzinfo=timezone.utc)
T2 = datetime(2026, 1, 3, tzinfo=timezone.utc)


def _version(urn, dataset, at, rows=1, producer=None):
    return {"urn": urn, "dataset_urn": dataset, "created_at": at, "row_count": rows, "producer": producer}


def test_graph_walks_ancestors_and_keeps_column_mapping():
    raw = "hl:acme:main:dataset:raw"
    clean = "hl:acme:main:dataset:clean"
    customer = "hl:acme:main:object-type:Customer"
    graph = assemble_graph(
        root=customer,
        depth=4,
        direction="both",
        edges=[
            {"source_urn": "ver-raw-1", "target_urn": "ver-clean-1", "relation": "derived_from", "source_column": "", "target_property": "", "created_at": T1},
            {"source_urn": "ver-clean-1", "target_urn": customer, "relation": "maps_to", "source_column": "", "target_property": "", "created_at": T1},
            {"source_urn": "ver-clean-1", "target_urn": customer, "relation": "maps_to", "source_column": "email", "target_property": "email", "created_at": T1},
        ],
        versions=[
            _version("ver-raw-1", raw, T1),
            _version("ver-clean-1", clean, T1, producer={"kind": "pipeline", "pipeline_name": "customers", "step_name": "clean", "function_name": "drop_nulls"}),
        ],
        datasets=[{"urn": raw, "display_name": "raw"}, {"urn": clean, "display_name": "clean"}],
        object_types=[{"urn": customer, "name": "Customer"}],
    )
    urns = {node["urn"] for node in graph["nodes"]}
    assert urns == {customer, clean, raw}
    clean_node = next(node for node in graph["nodes"] if node["urn"] == clean)
    assert clean_node["columns"] == [{"source_column": "email", "target_property": "email"}]
    assert clean_node["producer"]["function_name"] == "drop_nulls"
    assert clean_node["stale"] is False


def test_dataset_is_stale_when_input_has_a_newer_version():
    raw = "hl:acme:main:dataset:raw"
    clean = "hl:acme:main:dataset:clean"
    graph = assemble_graph(
        root=clean,
        depth=2,
        direction="upstream",
        edges=[
            {"source_urn": "ver-raw-1", "target_urn": "ver-clean-1", "relation": "derived_from", "source_column": "", "target_property": "", "created_at": T1},
        ],
        versions=[
            _version("ver-raw-1", raw, T0),
            _version("ver-raw-2", raw, T2),
            _version("ver-clean-1", clean, T1),
        ],
        datasets=[{"urn": raw, "display_name": "raw"}, {"urn": clean, "display_name": "clean"}],
        object_types=[],
    )
    clean_node = next(node for node in graph["nodes"] if node["urn"] == clean)
    assert clean_node["stale"] is True
    assert {node["urn"] for node in graph["nodes"]} == {clean, raw}


def test_depth_one_stops_at_the_immediate_neighbor():
    raw = "hl:acme:main:dataset:raw"
    mid = "hl:acme:main:dataset:mid"
    out = "hl:acme:main:dataset:out"
    graph = assemble_graph(
        root=out,
        depth=1,
        direction="upstream",
        edges=[
            {"source_urn": "ver-raw", "target_urn": "ver-mid", "relation": "derived_from", "source_column": "", "target_property": "", "created_at": T0},
            {"source_urn": "ver-mid", "target_urn": "ver-out", "relation": "derived_from", "source_column": "", "target_property": "", "created_at": T1},
        ],
        versions=[
            _version("ver-raw", raw, T0),
            _version("ver-mid", mid, T0),
            _version("ver-out", out, T1),
        ],
        datasets=[
            {"urn": raw, "display_name": "raw"},
            {"urn": mid, "display_name": "mid"},
            {"urn": out, "display_name": "out"},
        ],
        object_types=[],
    )
    assert {node["urn"] for node in graph["nodes"]} == {out, mid}
    mid_node = next(node for node in graph["nodes"] if node["urn"] == mid)
    assert mid_node["expandable"] is True


def test_property_changes_show_before_and_after_and_skip_structural_bag():
    changes = property_changes(
        {
            "status": "closed",
            "__structural__": {"links": [], "objects": [{"kind": "delete_object", "object_type": "Note", "instance_id": "9"}]},
        },
        {"status": {"existed": True, "value": "open"}},
    )
    assert changes[0] == {"property": "status", "before": "open", "after": "closed"}
    assert changes[1]["after"] == "delete_object 9"
    assert "__structural__" not in {change["property"] for change in changes}


def test_missing_prior_value_is_empty_before():
    changes = property_changes({"name": "Ada"}, {"name": {"existed": False}})
    assert changes == [{"property": "name", "before": None, "after": "Ada"}]
