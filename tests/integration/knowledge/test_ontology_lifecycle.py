"""Tests for Ontology Lifecycle."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import IDENTITY, KNOWLEDGE, _request, ontology_url, holon_url


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


def test_editor_can_propose_but_cannot_publish_a_version(jdoe_token: str) -> None:
    """Role separation: editors hold workspace `write` (draft/propose)."""
    status, draft = _request(
        "POST", ontology_url("/objectTypes/Supplier/versions"), token=jdoe_token, body={"description": "editor draft"}
    )
    assert status == 201, draft

    status, body = _request(
        "POST", ontology_url(f"/objectTypes/Supplier/versions/{draft['version']}/publish"), token=jdoe_token
    )
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body


def test_property_mapping_is_a_real_object_not_a_json_string(msmith_token: str) -> None:
    """Verifies that `GET /ontology/{name}` parses the JSONB `property_mapping`."""
    status, body = _request("GET", ontology_url("/objectTypes/Supplier"), token=msmith_token)
    assert status == 200, body
    assert isinstance(body["property_mapping"], dict), body


def test_draft_does_not_affect_live_definition_until_published(msmith_token: str) -> None:
    status, current = _request("GET", ontology_url("/objectTypes/Supplier"), token=msmith_token)
    assert status == 200, current

    marker = f"governance test marker {time.time()}"
    status, draft = _request(
        "POST", ontology_url("/objectTypes/Supplier/versions"), token=msmith_token, body={"description": marker}
    )
    assert status == 201, draft
    # Not necessarily exactly current+1: other tests (branching, interfaces)
    # may have left unpublished drafts at higher numbers on Supplier too
    # `propose_object_type_version` correctly skips past those to avoid a
    # version-number collision, it only guarantees *newer than current*.
    assert draft["version"] > current["version"], draft
    assert draft["status"] == "draft", draft
    # Partial update: property_mapping wasn't specified, must carry the
    # current published value forward unchanged.
    assert draft["property_mapping"] == current["property_mapping"], draft

    status, still_live = _request("GET", ontology_url("/objectTypes/Supplier"), token=msmith_token)
    assert status == 200
    assert still_live["description"] == current["description"], "draft must not leak into the live read"
    assert still_live["version"] == current["version"], still_live

    status, published = _request(
        "POST", ontology_url(f"/objectTypes/Supplier/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 200, published
    assert published["description"] == marker, published
    assert published["version"] == draft["version"], published

    status, now_live = _request("GET", ontology_url("/objectTypes/Supplier"), token=msmith_token)
    assert now_live["description"] == marker, now_live
    assert now_live["version"] == draft["version"], now_live


def test_republishing_an_already_published_version_is_rejected(msmith_token: str) -> None:
    status, draft = _request(
        "POST", ontology_url("/objectTypes/Supplier/versions"), token=msmith_token, body={"description": "second pass"}
    )
    assert status == 201, draft

    status, first_publish = _request(
        "POST", ontology_url(f"/objectTypes/Supplier/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 200, first_publish

    status, second_publish = _request(
        "POST", ontology_url(f"/objectTypes/Supplier/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 400, second_publish


def test_publishing_an_older_draft_than_live_is_rejected(msmith_token: str) -> None:
    """Monotonicity: once live is at vN, publishing a still-open draft."""
    status, older = _request(
        "POST", ontology_url("/objectTypes/Supplier/versions"), token=msmith_token,
        body={"description": f"older draft {time.time()}"},
    )
    assert status == 201, older

    status, newer = _request(
        "POST", ontology_url("/objectTypes/Supplier/versions"), token=msmith_token,
        body={"description": f"newer draft {time.time()}"},
    )
    assert status == 201, newer
    assert newer["version"] > older["version"], (older, newer)

    status, published = _request(
        "POST", ontology_url(f"/objectTypes/Supplier/versions/{newer['version']}/publish"), token=msmith_token
    )
    assert status == 200, published

    status, live = _request("GET", ontology_url("/objectTypes/Supplier"), token=msmith_token)
    assert live["version"] == newer["version"], live

    status, regress = _request(
        "POST", ontology_url(f"/objectTypes/Supplier/versions/{older['version']}/publish"), token=msmith_token
    )
    assert status == 400, regress
    assert "live is already at version" in regress["detail"], regress

    status, still_live = _request("GET", ontology_url("/objectTypes/Supplier"), token=msmith_token)
    assert still_live["version"] == newer["version"], still_live
    assert still_live["description"] == newer["description"], still_live


def test_version_history_lists_every_proposed_version(msmith_token: str) -> None:
    """Every version this file itself proposes gets published."""
    status, versions = _request("GET", ontology_url("/objectTypes/Supplier/versions"), token=msmith_token)
    assert status == 200, versions
    assert len(versions) >= 2, versions
    by_description = {v["description"]: v["status"] for v in versions}
    for marker in ("second pass",):
        assert by_description.get(marker) == "published", versions
