"""Collection membership must not disclose inaccessible resource URNs."""

from __future__ import annotations

from conftest import EXPERIENCE, TENANT_ID, _request, _unique_name


def test_collection_filters_members_the_viewer_cannot_read(jdoe_token: str, kenji_token: str) -> None:
    """Kenji can read the workspace and collection, but not an Application."""
    status, collection = _request(
        "POST", f"{EXPERIENCE}/api/collections", token=jdoe_token,
        body={"name": _unique_name("private-member"), "description": "authorization regression test"},
    )
    assert status == 200, collection
    collection_id = collection["id"]
    hidden_urn = f"hl:{TENANT_ID}:main:application:{_unique_name('unreadable')}"

    try:
        status, body = _request(
            "POST", f"{EXPERIENCE}/api/collections/{collection_id}/members/{hidden_urn}", token=jdoe_token,
        )
        assert status == 200, body

        status, owner_view = _request("GET", f"{EXPERIENCE}/api/collections/{collection_id}", token=jdoe_token)
        assert status == 200 and owner_view["members"] == [], owner_view

        status, viewer_view = _request("GET", f"{EXPERIENCE}/api/collections/{collection_id}", token=kenji_token)
        assert status == 200 and viewer_view["members"] == [], viewer_view

        status, body = _request(
            "GET", f"{EXPERIENCE}/api/resources/{hidden_urn}/collections", token=kenji_token,
        )
        assert status == 403, body
    finally:
        _request("DELETE", f"{EXPERIENCE}/api/collections/{collection_id}", token=jdoe_token)
