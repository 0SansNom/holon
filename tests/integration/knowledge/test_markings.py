"""Tests for Markings."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

import pytest
from conftest import IDENTITY, KNOWLEDGE, TENANT_ID, _request, ontology_url, holon_url


def _token_for(principal_urn: str) -> str:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        local_name = principal_urn.rsplit(":", 1)[-1]
        status, body = _request(
            "POST",
            f"{IDENTITY}/token",
            body={"principal_urn": principal_urn, "client_secret": f"{local_name}-dev-secret"},
        )
        if status == 200:
            return body["access_token"]
        time.sleep(1.5)
    pytest.fail(f"could not mint a token for {principal_urn}")


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _create_marking(token: str, description: str = "") -> str:
    name = _unique_name("marking")
    status, marking = _request("POST", holon_url("/markings"), token=token, body={"name": name, "description": description})
    assert status == 201, marking
    return name


def _grant_marking(token: str, marking_name: str, principal_urn: str) -> None:
    status, body = _request(
        "POST", holon_url(f"/markings/{marking_name}/principals/{principal_urn}/access/grant"), token=token,
    )
    assert status == 200, body


def _clear_object_type_scope(token: str, name: str) -> None:
    """Test teardown: publish an empty `markings`/`implements`-free draft."""
    status, draft = _request("POST", ontology_url(f"/objectTypes/{name}/versions"), token=token, body={"markings": []})
    assert status == 201, draft
    status, published = _request(
        "POST", ontology_url(f"/objectTypes/{name}/versions/{draft['version']}/publish"), token=token,
    )
    assert status == 200, published


def test_workspace_editor_cannot_register_a_marking(jdoe_token: str) -> None:
    status, body = _request("POST", holon_url("/markings"), token=jdoe_token, body={"name": _unique_name("marking")})
    assert status == 403, body


def test_workspace_admin_can_register_a_marking(msmith_token: str) -> None:
    name = _create_marking(msmith_token, description="test marking")
    status, fetched = _request("GET", holon_url(f"/markings/{name}"), token=msmith_token)
    assert status == 200, fetched
    assert fetched["name"] == name, fetched
    assert fetched.get("id"), fetched
    assert fetched.get("category_id"), fetched
    assert fetched.get("category_type") == "CONJUNCTIVE", fetched


def test_disjunctive_category_needs_only_one_hold(msmith_token: str, jdoe_token: str) -> None:
    """Two markings in one DISJUNCTIVE category on an ObjectType: holding."""
    status, cat = _request(
        "POST",
        holon_url("/marking-categories"),
        token=msmith_token,
        body={
            "name": _unique_name("disj-cat"),
            "category_type": "DISJUNCTIVE",
            "description": "region or",
        },
    )
    assert status == 201, cat

    m1 = _unique_name("mark-eu")
    m2 = _unique_name("mark-us")
    for name in (m1, m2):
        status, body = _request(
            "POST",
            holon_url("/markings"),
            token=msmith_token,
            body={"name": name, "category_id": cat["id"]},
        )
        assert status == 201, body

    try:
        status, draft = _request(
            "POST",
            ontology_url("/objectTypes/InventoryLevel/versions"),
            token=msmith_token,
            body={"description": "disjunctive-marking", "markings": [m1, m2]},
        )
        assert status == 201, draft
        status, published = _request(
            "POST",
            ontology_url(f"/objectTypes/InventoryLevel/versions/{draft['version']}/publish"),
            token=msmith_token,
        )
        assert status == 200, published

        status, denied = _request("GET", ontology_url("/objects/InventoryLevel"), token=jdoe_token)
        assert status == 403, denied

        _grant_marking(msmith_token, m1, f"hl:{TENANT_ID}:global:user:jdoe")

        status, rows = _request("GET", ontology_url("/objects/InventoryLevel"), token=jdoe_token)
        assert status == 200, rows
        assert len(rows) > 0, rows
    finally:
        _clear_object_type_scope(msmith_token, "InventoryLevel")


def test_publishing_with_an_unregistered_marking_is_rejected(msmith_token: str) -> None:
    status, draft = _request(
        "POST", ontology_url("/objectTypes/InventoryLevel/versions"), token=msmith_token,
        body={"markings": [_unique_name("nonexistent")]},
    )
    assert status == 201, draft
    status, body = _request(
        "POST", ontology_url(f"/objectTypes/InventoryLevel/versions/{draft['version']}/publish"), token=msmith_token,
    )
    assert status == 400, body
    assert "unknown marking" in body["detail"], body


def test_object_type_level_marking_blocks_until_granted_then_unblocks(
    msmith_token: str, jdoe_token: str, kenji_token: str
) -> None:
    """jdoe otherwise has full workspace-level read access to."""
    marking_name = _create_marking(msmith_token)
    try:
        status, draft = _request(
            "POST", ontology_url("/objectTypes/InventoryLevel/versions"), token=msmith_token,
            body={"description": "marking-gated", "markings": [marking_name]},
        )
        assert status == 201, draft
        status, published = _request(
            "POST", ontology_url(f"/objectTypes/InventoryLevel/versions/{draft['version']}/publish"), token=msmith_token,
        )
        assert status == 200, published
        assert published["markings"] == [marking_name], published

        status, denied = _request("GET", ontology_url("/objects/InventoryLevel"), token=jdoe_token)
        assert status == 403, denied
        assert "missing required marking" in denied["detail"], denied

        _grant_marking(msmith_token, marking_name, f"hl:{TENANT_ID}:global:user:jdoe")

        status, rows = _request("GET", ontology_url("/objects/InventoryLevel"), token=jdoe_token)
        assert status == 200, rows
        assert len(rows) > 0, rows

        status, still_denied = _request("GET", ontology_url("/objects/InventoryLevel"), token=kenji_token)
        assert still_denied is not None
        assert status == 403, still_denied
    finally:
        _clear_object_type_scope(msmith_token, "InventoryLevel")


def test_instance_level_marking_excludes_only_that_row(msmith_token: str, jdoe_token: str, kenji_token: str) -> None:
    marking_name = _create_marking(msmith_token)

    status, before = _request("GET", ontology_url("/objects/Supplier"), token=jdoe_token)
    assert status == 200, before
    target_id = before[0]["id"]

    status, body = _request(
        "POST", ontology_url(f"/objects/Supplier/{target_id}/markings"), token=msmith_token,
        body={"markings": [marking_name]},
    )
    assert status == 200, body
    assert body["markings"] == [marking_name], body

    try:
        status, after = _request("GET", ontology_url("/objects/Supplier"), token=jdoe_token)
        assert status == 200, after
        assert len(after) == len(before) - 1, (before, after)
        assert target_id not in [row["id"] for row in after], after

        status, single = _request("GET", ontology_url(f"/objects/Supplier/{target_id}"), token=jdoe_token)
        assert status == 404, single

        _grant_marking(msmith_token, marking_name, f"hl:{TENANT_ID}:global:user:jdoe")

        status, restored = _request("GET", ontology_url("/objects/Supplier"), token=jdoe_token)
        assert status == 200, restored
        assert target_id in [row["id"] for row in restored], restored

        # kenji was never granted
        # and every *other* Supplier must stay visible (not a wholesale deny).
        status, kenji_rows = _request("GET", ontology_url("/objects/Supplier"), token=kenji_token)
        assert status == 200, kenji_rows
        assert target_id not in [row["id"] for row in kenji_rows], kenji_rows
        assert len(kenji_rows) == len(before) - 1, kenji_rows
    finally:
        _request(
            "POST", ontology_url(f"/objects/Supplier/{target_id}/markings"), token=msmith_token, body={"markings": []}
        )
