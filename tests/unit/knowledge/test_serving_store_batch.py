"""Unit tests for batched serving-store materialize records."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_DIR = REPO_ROOT / "services" / "knowledge"
LIBS = REPO_ROOT / "libs"
sys.path.insert(0, str(LIBS))
sys.path.insert(0, str(KNOWLEDGE_DIR))

from app.serving_store import MATERIALIZE_BATCH_SIZE, _materialize_records  # noqa: E402


def test_materialize_records_json_and_string_ids() -> None:
    rows = [{"id": 1, "name": "a"}, {"id": "2", "name": "b"}]
    records = _materialize_records(
        object_type="Customer", tenant_id="acme", snapshot_id=99, rows=rows
    )
    assert len(records) == 2
    assert records[0][0] == "Customer"
    assert records[0][1] == "acme"
    assert records[0][2] == "1"
    assert json.loads(records[0][3])["name"] == "a"
    assert records[0][4] == 99
    assert records[1][2] == "2"


def test_materialize_records_empty() -> None:
    assert _materialize_records(object_type="X", tenant_id="t", snapshot_id=1, rows=[]) == []


def test_materialize_records_logs_collapsed_duplicates(caplog) -> None:
    import logging

    caplog.set_level(logging.WARNING, logger="knowledge.serving_store")
    rows = [{"id": 1, "name": "a"}, {"id": 1, "name": "b"}]
    records = _materialize_records(object_type="X", tenant_id="t", snapshot_id=1, rows=rows)
    assert len(records) == 1
    assert "collapsed" in caplog.text


def test_materialize_records_collapses_duplicate_ids_last_wins() -> None:
    rows = [
        {"id": 1, "name": "first"},
        {"id": 2, "name": "other"},
        {"id": 1, "name": "last"},
        {"id": "1", "name": "string-same-key"},
    ]
    records = _materialize_records(object_type="X", tenant_id="t", snapshot_id=7, rows=rows)
    assert [r[2] for r in records] == ["1", "2"]
    assert json.loads(records[0][3])["name"] == "string-same-key"
    assert json.loads(records[1][3])["name"] == "other"
    assert records[0][4] == 7


def test_materialize_batch_size_is_chunked() -> None:
    assert MATERIALIZE_BATCH_SIZE >= 100
    rows = [{"id": i} for i in range(MATERIALIZE_BATCH_SIZE + 3)]
    records = _materialize_records(object_type="X", tenant_id="t", snapshot_id=1, rows=rows)
    assert len(records) == MATERIALIZE_BATCH_SIZE + 3
    chunks = [
        records[i : i + MATERIALIZE_BATCH_SIZE]
        for i in range(0, len(records), MATERIALIZE_BATCH_SIZE)
    ]
    assert len(chunks) == 2
    assert len(chunks[0]) == MATERIALIZE_BATCH_SIZE
    assert len(chunks[1]) == 3
