"""Org/Space/Project hierarchy
— a new tier under Workspace. `project` is a first-class SpiceDB resource
(`docker/spicedb/schema.zed`): `parent_workspace` cascades workspace
access down, exactly like `object_type` already does one level up.
Scoping an ObjectType to a project (`project_urn`, settable via direct
publish or via branch/review merge) adds a `parent_project` relationship
*on top of* the ObjectType's existing `parent_workspace` one — a union,
not a replacement, so nothing already granted at the workspace tier is
ever silently revoked by adding a narrower project scope. Proves: only
workspace `approve` can create a project; a project-only grant reaches
objects scoped to that project without any workspace grant at all; that
same project-only grant does *not* leak to an unrelated, unscoped
ObjectType; and a pre-existing workspace grant keeps working on a
newly-scoped ObjectType. No real LLM calls.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

import pytest
from conftest import IDENTITY, KNOWLEDGE, TENANT_ID, _request


WORKSPACE_ID = "demo"


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


def _create_project(token: str) -> tuple[str, str]:
    name = _unique_name("proj")
    status, project = _request("POST", f"{IDENTITY}/projects", token=token, body={"name": name})
    assert status == 201, project
    return name, project["urn"]


def _grant_project_access(token: str, project_name: str, principal_urn: str, relation: str = "viewer") -> None:
    status, body = _request(
        "POST",
        f"{IDENTITY}/projects/{project_name}/principals/{principal_urn}/access/grant",
        token=token,
        body={"relation": relation},
    )
    assert status == 200, body


def _wait_until(fn, deadline_seconds: float = 20.0) -> None:
    """Grants propagate to Knowledge's authz decisions asynchronously
    (the `identity.permission.granted`-shaped event flowing through the
    same cache-invalidation consumer `.../revoke` already relies on) —
    poll rather than assume same-request-cycle consistency.
    """
    deadline = time.monotonic() + deadline_seconds
    last_exc = None
    while time.monotonic() < deadline:
        try:
            fn()
            return
        except AssertionError as exc:
            last_exc = exc
            time.sleep(1)
    raise last_exc


def test_workspace_editor_cannot_create_a_project(jdoe_token: str) -> None:
    status, body = _request("POST", f"{IDENTITY}/projects", token=jdoe_token, body={"name": _unique_name("proj")})
    assert status == 403, body


def test_workspace_admin_can_create_a_project(msmith_token: str) -> None:
    name, urn = _create_project(msmith_token)
    assert urn == f"hl:{TENANT_ID}:{WORKSPACE_ID}:project:{name}"

    status, fetched = _request("GET", f"{IDENTITY}/projects/{name}", token=msmith_token)
    assert status == 200, fetched
    assert fetched["urn"] == urn, fetched


def test_creating_a_duplicate_project_name_is_rejected(msmith_token: str) -> None:
    name, _ = _create_project(msmith_token)
    status, body = _request("POST", f"{IDENTITY}/projects", token=msmith_token, body={"name": name})
    assert status == 409, body


@pytest.mark.parametrize("name", ["", "contains/slash", "contains:colon", "-starts-with-separator"])
def test_invalid_project_names_are_rejected(msmith_token: str, name: str) -> None:
    status, body = _request("POST", f"{IDENTITY}/projects", token=msmith_token, body={"name": name})
    assert status == 422, body


def test_project_only_grant_reaches_a_project_scoped_object_type_without_any_workspace_grant(
    jdoe_token: str, msmith_token: str, alice_token: str
) -> None:
    """alice holds no workspace relation at all (a tenant member only) —
    proves `parent_project->read` genuinely stands on its own, not as a
    disguised workspace check. Also proves the grant doesn't leak to an
    unrelated, unscoped ObjectType (Customer) — a project-tier grant is
    not a backdoor to every ObjectType in the workspace.
    """
    project_name, project_urn = _create_project(msmith_token)

    branch_name = _unique_name("scope-branch")
    status, branch = _request(
        "POST", f"{KNOWLEDGE}/ontology/InventoryLevel/branches", token=jdoe_token,
        body={"branch_name": branch_name, "project_urn": project_urn},
    )
    assert status == 201, branch

    status, review = _request(
        "POST", f"{KNOWLEDGE}/ontology/InventoryLevel/branches/{branch_name}/review", token=msmith_token,
        body={"decision": "approved"},
    )
    assert status == 200, review
    assert review["status"] == "merged", review

    status, live = _request("GET", f"{KNOWLEDGE}/ontology/InventoryLevel", token=msmith_token)
    assert status == 200
    assert live["project_urn"] == project_urn, live

    status, denied = _request("GET", f"{KNOWLEDGE}/objects/InventoryLevel", token=alice_token)
    assert status == 403, denied

    _grant_project_access(msmith_token, project_name, f"hl:{TENANT_ID}:global:user:alice")

    def _alice_can_read_inventory_level() -> None:
        status, body = _request("GET", f"{KNOWLEDGE}/objects/InventoryLevel", token=alice_token)
        assert status == 200, body

    _wait_until(_alice_can_read_inventory_level)

    status, denied = _request("GET", f"{KNOWLEDGE}/objects/Customer", token=alice_token)
    assert status == 403, denied
    assert "rebac_denied" in denied["detail"], denied


def test_publishing_with_an_unknown_project_urn_is_rejected(jdoe_token: str, msmith_token: str) -> None:
    bogus_urn = f"hl:{TENANT_ID}:{WORKSPACE_ID}:project:{_unique_name('nonexistent')}"
    status, draft = _request(
        "POST", f"{KNOWLEDGE}/ontology/InventoryLevel/versions", token=msmith_token,
        body={"description": "bogus project scope", "project_urn": bogus_urn},
    )
    assert status == 201, draft

    status, body = _request(
        "POST", f"{KNOWLEDGE}/ontology/InventoryLevel/versions/{draft['version']}/publish", token=msmith_token,
    )
    assert status == 400, body
    assert "unknown project" in body["detail"], body


def test_existing_workspace_grant_still_reads_a_newly_project_scoped_object_type(
    jdoe_token: str, msmith_token: str
) -> None:
    """Scoping InventoryLevel to a project must never revoke jdoe's own,
    separately-granted workspace access — a union, not a replacement.
    """
    project_name, project_urn = _create_project(msmith_token)

    status, draft = _request(
        "POST", f"{KNOWLEDGE}/ontology/InventoryLevel/versions", token=msmith_token,
        body={"description": "re-scoped for union check", "project_urn": project_urn},
    )
    assert status == 201, draft
    status, published = _request(
        "POST", f"{KNOWLEDGE}/ontology/InventoryLevel/versions/{draft['version']}/publish", token=msmith_token,
    )
    assert status == 200, published
    assert published["project_urn"] == project_urn, published

    status, body = _request("GET", f"{KNOWLEDGE}/objects/InventoryLevel", token=jdoe_token)
    assert status == 200, body
