"""Tests for Access Introspection."""

from __future__ import annotations

from conftest import IDENTITY, TENANT_ID, _request, _unique_name

ALICE_URN = f"hl:{TENANT_ID}:global:user:alice"
KENJI_URN = f"hl:{TENANT_ID}:global:user:kenji"


def _relations_for(body: list[dict], principal_urn: str) -> list[str]:
    return sorted(g["relation"] for g in body if g["principal_urn"] == principal_urn)


def test_workspace_access_listing_requires_admin(jdoe_token: str, kenji_token: str) -> None:
    """The membership list is itself sensitive."""
    for token in (jdoe_token, kenji_token):
        status, body = _request("GET", f"{IDENTITY}/access", token=token)
        assert status == 403, body


def test_workspace_access_listing_reflects_grant_and_revoke(msmith_token: str) -> None:
    """Self-cleaning round trip on alice (tenant member with no seeded."""
    status, body = _request("GET", f"{IDENTITY}/access", token=msmith_token)
    assert status == 200, body
    assert _relations_for(body, ALICE_URN) == [], body

    # Seeded grants are enriched from the principal table, not raw tuples.
    kenji = [g for g in body if g["principal_urn"] == KENJI_URN]
    assert kenji and kenji[0]["relation"] == "viewer", body
    assert kenji[0]["display_name"] == "Kenji Sato", kenji
    assert kenji[0]["type"] == "user", kenji

    status, body = _request(
        "POST", f"{IDENTITY}/principals/{ALICE_URN}/access/grant", token=msmith_token, body={"relation": "viewer"}
    )
    assert status == 200, body

    status, body = _request("GET", f"{IDENTITY}/access", token=msmith_token)
    assert status == 200, body
    assert _relations_for(body, ALICE_URN) == ["viewer"], body

    status, body = _request(
        "POST", f"{IDENTITY}/principals/{ALICE_URN}/access/revoke", token=msmith_token, body={"relation": "viewer"}
    )
    assert status == 200, body

    status, body = _request("GET", f"{IDENTITY}/access", token=msmith_token)
    assert status == 200, body
    assert _relations_for(body, ALICE_URN) == [], body


def test_project_access_listing(msmith_token: str, jdoe_token: str) -> None:
    """Direct grants only: alice's project-scoped viewer shows up here."""
    project_name = _unique_name("acl")
    status, body = _request("POST", f"{IDENTITY}/projects", token=msmith_token, body={"name": project_name})
    assert status == 201, body

    # Unknown project 404s (the governance guard's own lookup).
    status, body = _request("GET", f"{IDENTITY}/projects/{_unique_name('nope')}/access", token=msmith_token)
    assert status == 404, body

    # jdoe is workspace editor
    # workspace admins, and he is neither on this fresh project.
    status, body = _request("GET", f"{IDENTITY}/projects/{project_name}/access", token=jdoe_token)
    assert status == 403, body

    status, body = _request("GET", f"{IDENTITY}/projects/{project_name}/access", token=msmith_token)
    assert status == 200, body
    assert body == [], body  # only the parent_workspace edge exists — hierarchy, not a grant

    status, body = _request(
        "POST",
        f"{IDENTITY}/projects/{project_name}/principals/{ALICE_URN}/access/grant",
        token=msmith_token,
        body={"relation": "viewer"},
    )
    assert status == 200, body

    status, body = _request("GET", f"{IDENTITY}/projects/{project_name}/access", token=msmith_token)
    assert status == 200, body
    assert _relations_for(body, ALICE_URN) == ["viewer"], body
    alice = next(g for g in body if g["principal_urn"] == ALICE_URN)
    assert alice["display_name"] == "Alice TenantMember", alice

    status, body = _request(
        "POST",
        f"{IDENTITY}/projects/{project_name}/principals/{ALICE_URN}/access/revoke",
        token=msmith_token,
        body={"relation": "viewer"},
    )
    assert status == 200, body

    status, body = _request("GET", f"{IDENTITY}/projects/{project_name}/access", token=msmith_token)
    assert status == 200, body
    assert body == [], body
