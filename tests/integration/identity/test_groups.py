"""Groups as first-class principals (SAS §1.4)."""

from __future__ import annotations

import time

from conftest import IDENTITY, TENANT_ID, _request, _unique_name, ontology_url


def test_group_cannot_mint_a_token(msmith_token: str) -> None:
    name = _unique_name("grp")
    status, group = _request(
        "POST",
        f"{IDENTITY}/principals",
        token=msmith_token,
        body={
            "tenant_id": TENANT_ID,
            "type": "group",
            "local_name": name,
            "display_name": "Analysts",
        },
    )
    assert status == 201, group
    assert group["type"] == "group", group
    assert "client_secret" not in group, group

    status, body = _request(
        "POST",
        f"{IDENTITY}/token",
        body={"principal_urn": group["urn"], "client_secret": "unset"},
    )
    assert status == 403, body
    assert body.get("errorName") == "GroupCannotAuthenticate", body


def test_nested_groups_are_rejected(msmith_token: str) -> None:
    parent = _unique_name("parent")
    child = _unique_name("child")
    status, parent_row = _request(
        "POST",
        f"{IDENTITY}/principals",
        token=msmith_token,
        body={"tenant_id": TENANT_ID, "type": "group", "local_name": parent, "display_name": "Parent"},
    )
    assert status == 201, parent_row
    status, child_row = _request(
        "POST",
        f"{IDENTITY}/principals",
        token=msmith_token,
        body={"tenant_id": TENANT_ID, "type": "group", "local_name": child, "display_name": "Child"},
    )
    assert status == 201, child_row

    status, body = _request(
        "POST",
        f"{IDENTITY}/principals/{parent_row['urn']}/members",
        token=msmith_token,
        body={"principal_urn": child_row["urn"]},
    )
    assert status == 400, body
    assert body.get("errorName") == "NestedGroupForbidden", body


def test_group_membership_grants_workspace_read_to_members(msmith_token: str) -> None:
    """A fresh user with no workspace grant inherits viewer via a group."""
    local = _unique_name("member")
    status, user = _request(
        "POST",
        f"{IDENTITY}/principals",
        token=msmith_token,
        body={
            "tenant_id": TENANT_ID,
            "type": "user",
            "local_name": local,
            "display_name": "Group Member",
            "country": "FR",
        },
    )
    assert status == 201, user
    status, token_body = _request(
        "POST", f"{IDENTITY}/token", body={"principal_urn": user["urn"], "client_secret": f"{local}-dev-secret"}
    )
    assert status == 200, token_body
    member_token = token_body["access_token"]

    status, denied = _request("GET", ontology_url("/objects/ProductReview"), token=member_token)
    assert status == 403, denied

    name = _unique_name("readers")
    status, group = _request(
        "POST",
        f"{IDENTITY}/principals",
        token=msmith_token,
        body={"tenant_id": TENANT_ID, "type": "group", "local_name": name, "display_name": "Readers"},
    )
    assert status == 201, group

    status, added = _request(
        "POST",
        f"{IDENTITY}/principals/{group['urn']}/members",
        token=msmith_token,
        body={"principal_urn": user["urn"]},
    )
    assert status == 201, added

    status, members = _request("GET", f"{IDENTITY}/principals/{group['urn']}/members", token=msmith_token)
    assert status == 200, members
    assert any(m["principal_urn"] == user["urn"] for m in members), members

    status, grant = _request(
        "POST",
        f"{IDENTITY}/principals/{group['urn']}/access/grant",
        token=msmith_token,
        body={"relation": "viewer"},
    )
    assert status == 200, grant

    status, listing = _request("GET", f"{IDENTITY}/access", token=msmith_token)
    assert status == 200, listing
    assert any(g["principal_urn"] == group["urn"] and g["relation"] == "viewer" for g in listing), listing

    deadline = time.monotonic() + 15
    status, rows = 403, {}
    while time.monotonic() < deadline:
        status, rows = _request("GET", ontology_url("/objects/ProductReview"), token=member_token)
        if status == 200:
            break
        time.sleep(0.5)
    assert status == 200, rows

    status, removed = _request(
        "DELETE",
        f"{IDENTITY}/principals/{group['urn']}/members/{user['urn']}",
        token=msmith_token,
    )
    assert status == 200, removed

    deadline = time.monotonic() + 15
    status, denied_again = 200, {}
    while time.monotonic() < deadline:
        status, denied_again = _request("GET", ontology_url("/objects/ProductReview"), token=member_token)
        if status == 403:
            break
        time.sleep(0.5)
    assert status == 403, denied_again
