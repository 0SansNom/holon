"""Unit tests for Holon keyset paging (no live stack)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIBS = Path(__file__).resolve().parent.parent / "libs"
KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "services" / "knowledge"
sys.path.insert(0, str(LIBS))
sys.path.insert(0, str(KNOWLEDGE_DIR))

from app import paging  # noqa: E402


def _rows(*ids):
    return [{"id": i, "name": f"n{i}"} for i in ids]


def test_paginate_first_page_and_cursor_chain():
    rows = _rows(1, 2, 3, 4, 5)
    page1 = paging.paginate_rows(rows, page_size=2)
    assert [r["id"] for r in page1["items"]] == [1, 2]
    assert page1["page_size"] == 2
    assert page1["next_cursor"]

    page2 = paging.paginate_rows(rows, page_size=2, cursor=page1["next_cursor"])
    assert [r["id"] for r in page2["items"]] == [3, 4]
    assert page2["next_cursor"]

    page3 = paging.paginate_rows(rows, page_size=2, cursor=page2["next_cursor"])
    assert [r["id"] for r in page3["items"]] == [5]
    assert page3["next_cursor"] is None


def test_page_size_never_exceeded():
    rows = _rows(*range(1, 20))
    page = paging.paginate_rows(rows, page_size=3)
    assert len(page["items"]) <= 3


def test_numeric_ids_sort_numerically():
    rows = _rows(10, 2, 1)
    page = paging.paginate_rows(rows, page_size=10)
    assert [r["id"] for r in page["items"]] == [1, 2, 10]


def test_invalid_cursor_raises():
    with pytest.raises(paging.PagingError):
        paging.paginate_rows(_rows(1), page_size=1, cursor="not-a-cursor")


def test_interface_key_avoids_id_collisions():
    rows = [
        {"id": 1, "_objectType": "Order", "name": "o"},
        {"id": 1, "_objectType": "Customer", "name": "c"},
        {"id": 2, "_objectType": "Customer", "name": "c2"},
    ]
    page1 = paging.paginate_rows(rows, page_size=2, key_of=paging.interface_instance_key)
    assert len(page1["items"]) == 2
    assert page1["next_cursor"]
    page2 = paging.paginate_rows(
        rows, page_size=2, cursor=page1["next_cursor"], key_of=paging.interface_instance_key
    )
    assert len(page2["items"]) == 1
    assert page2["next_cursor"] is None
