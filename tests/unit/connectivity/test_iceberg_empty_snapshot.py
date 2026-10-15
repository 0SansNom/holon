"""Empty refreshes clear the table, and an all-null column does not fail the write."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pyarrow as pa
import pytest
from pyiceberg.exceptions import NoSuchTableError

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app import iceberg_writer  # noqa: E402

pytestmark = pytest.mark.unit

_KW = dict(
    tenant_id="acme",
    catalog_uri="http://catalog",
    warehouse="wh",
    s3_endpoint="http://s3",
    access_key="a",
    secret_key="s",
    region="r",
)


def test_all_null_column_is_stored_as_string() -> None:
    table = iceberg_writer._table_from_rows([{"id": 1, "note": None}])
    assert table.schema.field("note").type == pa.string()
    assert table.column("note")[0].as_py() is None


def test_empty_overwrite_of_an_existing_table_clears_it(monkeypatch: pytest.MonkeyPatch) -> None:
    schema = pa.schema([pa.field("id", pa.int64())])
    written = {}

    class _Schema:
        def as_arrow(self):
            return schema

    class _Table:
        def schema(self):
            return _Schema()

        def overwrite(self, arrow):
            written["arrow"] = arrow

        def refresh(self):
            return None

        def current_snapshot(self):
            return SimpleNamespace(snapshot_id=7, summary={"total-records": "0"})

        def location(self):
            return "s3://bucket/customers"

    catalog = MagicMock()
    catalog.load_table.return_value = _Table()
    monkeypatch.setattr(iceberg_writer, "_load_catalog", lambda *args, **kwargs: catalog)

    result = iceberg_writer.write_snapshot([], "customers", mode="overwrite", **_KW)
    assert result.row_count == 0
    assert result.snapshot_id == 7
    assert written["arrow"].num_rows == 0
    assert written["arrow"].schema == schema


def test_empty_overwrite_without_a_table_is_a_zero_row_success(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = MagicMock()
    catalog.load_table.side_effect = NoSuchTableError()
    catalog.rename_table.side_effect = NoSuchTableError()
    monkeypatch.setattr(iceberg_writer, "_load_catalog", lambda *args, **kwargs: catalog)

    result = iceberg_writer.write_snapshot([], "customers", mode="overwrite", **_KW)
    assert result.row_count == 0
    assert result.snapshot_id == 0
    catalog.create_table_if_not_exists.assert_not_called()


def test_append_is_not_retried_after_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    class _Table:
        def append(self, arrow):
            calls["n"] += 1
            raise RuntimeError("timed out after the commit was sent")

        def refresh(self):
            return None

        def location(self):
            return "s3://bucket/customers"

    catalog = MagicMock()
    catalog.load_table.return_value = _Table()
    catalog.create_table_if_not_exists.return_value = _Table()
    monkeypatch.setattr(iceberg_writer, "_load_catalog", lambda *args, **kwargs: catalog)

    with pytest.raises(RuntimeError, match="timed out"):
        iceberg_writer.write_snapshot([{"id": 1}], "customers", mode="append", **_KW)
    assert calls["n"] == 1
