"""Unit tests for materialized join-dataset pairs."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_DIR = REPO_ROOT / "services" / "knowledge"
LIBS = REPO_ROOT / "libs"
sys.path.insert(0, str(LIBS))
sys.path.insert(0, str(KNOWLEDGE_DIR))

from app.relation_links import pairs_from_join_rows  # noqa: E402
from app.link_overlays import merge_pair_set, overlays_absorbed_by_pairs  # noqa: E402


def test_pairs_from_join_rows_skips_nulls_and_dedupes() -> None:
    rows = [
        {"customer_id": 1, "order_id": 10},
        {"customer_id": 1, "order_id": 10},
        {"customer_id": None, "order_id": 11},
        {"customer_id": 2, "order_id": None},
        {"customer_id": 2, "order_id": 12},
    ]
    assert pairs_from_join_rows(rows, "customer_id", "order_id") == [("1", "10"), ("2", "12")]


def test_overlays_merge_onto_materialized_pairs_for_one_instance() -> None:
    current = "1"
    base_ids = ["10", "11"]
    base_pairs = [(current, nid) for nid in base_ids]
    overlays = [
        {"source_id": "1", "target_id": "99", "op": "add"},
        {"source_id": "1", "target_id": "10", "op": "delete"},
        {"source_id": "2", "target_id": "50", "op": "add"},
    ]
    pairs = merge_pair_set(base_pairs, overlays)
    neighbor_ids = [t for s, t in pairs if s == current]
    assert neighbor_ids == ["11", "99"]


def test_overlays_absorbed_when_iceberg_caught_up() -> None:
    iceberg = {("1", "10"), ("1", "11")}
    overlays = [
        {"source_id": "1", "target_id": "10", "op": "add"},
        {"source_id": "1", "target_id": "11", "op": "delete"},
        {"source_id": "1", "target_id": "99", "op": "add"},
        {"source_id": "1", "target_id": "12", "op": "delete"},
    ]
    assert set(overlays_absorbed_by_pairs(overlays, iceberg)) == {("1", "10"), ("1", "12")}
