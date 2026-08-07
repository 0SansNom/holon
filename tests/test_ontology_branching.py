"""Ontology branching + review
— the same human-in-the-loop shape Actions already use (`action_approval`:
a `write`-tier request, an `approve`-tier decision, role separation
rather than a same-URN check), applied to ontology changes instead of
data writes. Proves: an editor can branch but not review; `changes_requested`
leaves the branch open for a follow-up draft; `approved` merges through
the *existing* `publish_object_type_version` (so `implements`/
`derived_properties` validation still applies); a merged branch can't
be reviewed again. No real LLM calls.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

import pytest
from conftest import IDENTITY, KNOWLEDGE, _request


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


def _unique_branch(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def test_viewer_cannot_create_a_branch(kenji_token: str) -> None:
    status, body = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/branches", token=kenji_token,
        body={"branch_name": _unique_branch("denied")},
    )
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body


def test_editor_can_create_a_branch(jdoe_token: str) -> None:
    branch_name = _unique_branch("editor-branch")
    status, branch = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/branches", token=jdoe_token,
        body={"branch_name": branch_name, "description": "editor-created branch"},
    )
    assert status == 201, branch
    assert branch["status"] == "open", branch
    assert branch["created_by_urn"].endswith(":jdoe"), branch

    status, fetched = _request("GET", f"{KNOWLEDGE}/ontology/Supplier/branches/{branch_name}", token=jdoe_token)
    assert status == 200, fetched
    assert fetched["branch_name"] == branch_name, fetched


def test_creating_a_duplicate_branch_name_is_rejected(jdoe_token: str) -> None:
    branch_name = _unique_branch("dup")
    status, _ = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/branches", token=jdoe_token, body={"branch_name": branch_name}
    )
    assert status == 201
    status, body = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/branches", token=jdoe_token, body={"branch_name": branch_name}
    )
    assert status == 400, body


def test_editor_cannot_review_their_own_branch(jdoe_token: str) -> None:
    """Role separation, not a same-URN check: jdoe is an editor and
    simply doesn't hold workspace `approve` at all, regardless of who
    created the branch.
    """
    branch_name = _unique_branch("self-review")
    status, _ = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/branches", token=jdoe_token, body={"branch_name": branch_name}
    )
    assert status == 201
    status, body = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/branches/{branch_name}/review", token=jdoe_token,
        body={"decision": "approved"},
    )
    assert status == 403, body


def test_changes_requested_leaves_branch_open_for_a_follow_up_draft(jdoe_token: str, msmith_token: str) -> None:
    branch_name = _unique_branch("iterate")
    status, branch = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/branches", token=jdoe_token,
        body={"branch_name": branch_name, "description": "first pass"},
    )
    assert status == 201, branch
    first_version = branch["version"]

    status, review = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/branches/{branch_name}/review", token=msmith_token,
        body={"decision": "changes_requested", "note": "needs more detail"},
    )
    assert status == 200, review
    assert review["status"] == "open", review

    status, updated = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/branches/{branch_name}/draft", token=jdoe_token,
        body={"description": "second pass, addressing feedback"},
    )
    assert status == 200, updated
    assert updated["version"] == first_version + 1, updated
    assert updated["status"] == "open", updated

    status, reviews = _request("GET", f"{KNOWLEDGE}/ontology/Supplier/branches/{branch_name}/reviews", token=jdoe_token)
    assert status == 200
    assert len(reviews) == 1, reviews
    assert reviews[0]["decision"] == "changes_requested", reviews


def test_approved_review_merges_through_the_real_publish_path(jdoe_token: str, msmith_token: str) -> None:
    branch_name = _unique_branch("merge")
    marker = f"merged via branch review {time.time()}"
    status, branch = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/branches", token=jdoe_token,
        body={"branch_name": branch_name, "description": marker},
    )
    assert status == 201, branch

    status, review = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/branches/{branch_name}/review", token=msmith_token,
        body={"decision": "approved", "note": "ship it"},
    )
    assert status == 200, review
    assert review["status"] == "merged", review

    status, live = _request("GET", f"{KNOWLEDGE}/ontology/Supplier", token=jdoe_token)
    assert status == 200
    assert live["description"] == marker, live
    assert live["version"] == branch["version"], live

    # Re-reviewing a merged branch must be rejected — it's a terminal state.
    status, second_review = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/branches/{branch_name}/review", token=msmith_token,
        body={"decision": "approved"},
    )
    assert status == 400, second_review


def test_merge_still_enforces_implements_validation(jdoe_token: str, msmith_token: str) -> None:
    """The branch/review path calls the *same* `publish_object_type_version`
    — an interface conformance a branch can't actually satisfy must still
    be rejected at merge time, not silently accepted just because it went
    through review.
    """
    interface_name = _unique_branch("RequiresLifetimeValue").replace("-", "")
    status, _ = _request(
        "POST", f"{KNOWLEDGE}/interfaces", token=msmith_token,
        body={"name": interface_name, "required_properties": ["lifetimeValue"]},
    )
    assert status == 201

    branch_name = _unique_branch("bad-implements")
    status, branch = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/branches", token=jdoe_token,
        body={"branch_name": branch_name, "implements": [interface_name]},
    )
    assert status == 201, branch

    status, review = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/branches/{branch_name}/review", token=msmith_token,
        body={"decision": "approved"},
    )
    assert status == 400, review
    assert "missing required property" in review["detail"], review

    status, branch_after = _request("GET", f"{KNOWLEDGE}/ontology/Supplier/branches/{branch_name}", token=jdoe_token)
    assert branch_after["status"] == "open", "a rejected merge must not mark the branch merged"
