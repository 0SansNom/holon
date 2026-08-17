"""Live-stack smoke for Ontology collection cursor paging."""

from __future__ import annotations

from conftest import KNOWLEDGE, _request, ontology_url, holon_url


def test_customer_list_pages_with_cursor(jdoe_token: str) -> None:
    status, page1 = _request(
        "GET",
        ontology_url("/objects/Customer?pageSize=2"),
        token=jdoe_token,
        unwrap_pages=False,
    )
    assert status == 200, page1
    assert isinstance(page1, dict), page1
    assert "data" in page1 and "nextPageToken" in page1 and "pageSize" in page1
    assert page1["pageSize"] == 2
    assert len(page1["data"]) <= 2
    assert len(page1["data"]) >= 1
    ids1 = [row["id"] for row in page1["data"]]

    if not page1["nextPageToken"]:
        return

    status, page2 = _request(
        "GET",
        ontology_url(f"/objects/Customer?pageSize=2&pageToken={page1['nextPageToken']}"),
        token=jdoe_token,
        unwrap_pages=False,
    )
    assert status == 200, page2
    ids2 = [row["id"] for row in page2["data"]]
    assert not set(ids1) & set(ids2), (ids1, ids2)
    assert len(page2["data"]) <= 2


def test_customer_list_rejects_bad_cursor(jdoe_token: str) -> None:
    status, body = _request(
        "GET",
        ontology_url("/objects/Customer?pageSize=2&pageToken=not-valid"),
        token=jdoe_token,
        unwrap_pages=False,
    )
    assert status == 400, body
