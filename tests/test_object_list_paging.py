"""Live-stack smoke for Ontology collection cursor paging."""

from __future__ import annotations

from conftest import KNOWLEDGE, _request


def test_customer_list_pages_with_cursor(jdoe_token: str) -> None:
    status, page1 = _request(
        "GET",
        f"{KNOWLEDGE}/objects/Customer?page_size=2",
        token=jdoe_token,
        unwrap_pages=False,
    )
    assert status == 200, page1
    assert isinstance(page1, dict), page1
    assert "items" in page1 and "next_cursor" in page1 and "page_size" in page1
    assert page1["page_size"] == 2
    assert len(page1["items"]) <= 2
    assert len(page1["items"]) >= 1
    ids1 = [row["id"] for row in page1["items"]]

    if not page1["next_cursor"]:
        return

    status, page2 = _request(
        "GET",
        f"{KNOWLEDGE}/objects/Customer?page_size=2&cursor={page1['next_cursor']}",
        token=jdoe_token,
        unwrap_pages=False,
    )
    assert status == 200, page2
    ids2 = [row["id"] for row in page2["items"]]
    assert not set(ids1) & set(ids2), (ids1, ids2)
    assert len(page2["items"]) <= 2


def test_customer_list_rejects_bad_cursor(jdoe_token: str) -> None:
    status, body = _request(
        "GET",
        f"{KNOWLEDGE}/objects/Customer?page_size=2&cursor=not-valid",
        token=jdoe_token,
        unwrap_pages=False,
    )
    assert status == 400, body
