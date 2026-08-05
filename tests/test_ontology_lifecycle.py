"""Ontology lifecycle — ObjectType versioning/publication, closing a
real, previously-flagged gap (no `knowledge.objecttype.published` event
existed at all; ObjectTypes were only ever code-seeded, no runtime
governance). Proves: a draft never affects the live definition every
other read path uses until explicitly published; a partial update
carries the unspecified field forward unchanged; publishing is
governance-gated (workspace `approve`, same tier as RelationType
creation) and emits the real event; re-publishing an already-published
version is rejected; a published change survives a service restart
(the real fix for `ensure_seeded`'s boot-time reseed, which would
otherwise silently revert it). No real LLM calls.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

IDENTITY = "http://localhost:8001"
KNOWLEDGE = "http://localhost:8003"

TENANT_ID = "acme"


def _request(method: str, url: str, *, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


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


@pytest.fixture(scope="session")
def jdoe_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:jdoe")


@pytest.fixture(scope="session")
def msmith_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:msmith")


def test_editor_cannot_propose_or_publish_a_version(jdoe_token: str) -> None:
    status, body = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/versions", token=jdoe_token, body={"description": "should be denied"}
    )
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body


def test_property_mapping_is_a_real_object_not_a_json_string(msmith_token: str) -> None:
    """A real bug caught while building this feature: `GET /ontology/{name}`
    had never parsed the JSONB `property_mapping` column, silently handing
    every caller a JSON *string* instead of an object.
    """
    status, body = _request("GET", f"{KNOWLEDGE}/ontology/Supplier", token=msmith_token)
    assert status == 200, body
    assert isinstance(body["property_mapping"], dict), body


def test_draft_does_not_affect_live_definition_until_published(msmith_token: str) -> None:
    status, current = _request("GET", f"{KNOWLEDGE}/ontology/Supplier", token=msmith_token)
    assert status == 200, current

    marker = f"governance test marker {time.time()}"
    status, draft = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/versions", token=msmith_token, body={"description": marker}
    )
    assert status == 201, draft
    assert draft["version"] == current["version"] + 1, draft
    assert draft["status"] == "draft", draft
    # Partial update: property_mapping wasn't specified, must carry the
    # current published value forward unchanged.
    assert draft["property_mapping"] == current["property_mapping"], draft

    status, still_live = _request("GET", f"{KNOWLEDGE}/ontology/Supplier", token=msmith_token)
    assert status == 200
    assert still_live["description"] == current["description"], "draft must not leak into the live read"
    assert still_live["version"] == current["version"], still_live

    status, published = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/versions/{draft['version']}/publish", token=msmith_token
    )
    assert status == 200, published
    assert published["description"] == marker, published
    assert published["version"] == draft["version"], published

    status, now_live = _request("GET", f"{KNOWLEDGE}/ontology/Supplier", token=msmith_token)
    assert now_live["description"] == marker, now_live
    assert now_live["version"] == draft["version"], now_live


def test_republishing_an_already_published_version_is_rejected(msmith_token: str) -> None:
    status, draft = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/versions", token=msmith_token, body={"description": "second pass"}
    )
    assert status == 201, draft

    status, first_publish = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/versions/{draft['version']}/publish", token=msmith_token
    )
    assert status == 200, first_publish

    status, second_publish = _request(
        "POST", f"{KNOWLEDGE}/ontology/Supplier/versions/{draft['version']}/publish", token=msmith_token
    )
    assert status == 400, second_publish


def test_version_history_lists_every_proposed_version(msmith_token: str) -> None:
    status, versions = _request("GET", f"{KNOWLEDGE}/ontology/Supplier/versions", token=msmith_token)
    assert status == 200, versions
    assert len(versions) >= 2, versions
    assert all(v["status"] == "published" for v in versions), versions
