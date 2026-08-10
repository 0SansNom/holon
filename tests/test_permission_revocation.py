"""End-to-end verification of permission revocation (`identity.permission.revoked`).
Black-box over HTTP, same style as the other test modules. Requires the stack running (`make up`).

Note on propagation: decision cache means a revocation is no longer visible on
the *literal* next request — it propagates via `identity.permission.revoked`
through the outbox relay (up to its 1s poll interval) to Knowledge's
invalidation consumer, and the cache's own 5s TTL is a second, independent
upper bound even if that event were somehow missed. Both are comfortably
inside propagation limits, so this polls for convergence (same idiom used
everywhere else in this suite for genuinely-async effects) rather than
asserting on the very next call.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import IDENTITY, KNOWLEDGE, TENANT_ID, _request


KENJI_URN = f"hl:{TENANT_ID}:global:user:kenji"


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
def kenji_token() -> str:
    return _token_for(KENJI_URN)


def _kenji_can_read_reviews(kenji_token: str) -> bool:
    status, _ = _request("GET", f"{KNOWLEDGE}/objects/ProductReview", token=kenji_token)
    return status == 200


def _poll_until(predicate, *, timeout: float = 10.0) -> bool:
    """The decision cache's TTL alone bounds
    staleness at 5s even if the invalidation event were somehow missed —
    10s here is a comfortable margin for both."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.5)
    return predicate()


def test_revoke_then_grant_kenji_viewer_access(msmith_token: str, kenji_token: str) -> None:
    """The full round trip, self-cleaning: revoke, prove denial, grant
    back, prove restored — kenji's seeded access must be exactly as it
    was before this test ran, for every other test module's sake.
    """
    assert _kenji_can_read_reviews(kenji_token), "precondition: kenji should start with read access"

    status, body = _request(
        "POST", f"{IDENTITY}/principals/{KENJI_URN}/access/revoke", token=msmith_token, body={"relation": "viewer"}
    )
    assert status == 200, body
    assert body["status"] == "revoked", body

    assert _poll_until(lambda: not _kenji_can_read_reviews(kenji_token)), "revocation never propagated within budget"
    status, body = _request("GET", f"{KNOWLEDGE}/objects/ProductReview", token=kenji_token)
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body

    status, body = _request(
        "POST", f"{IDENTITY}/principals/{KENJI_URN}/access/grant", token=msmith_token, body={"relation": "viewer"}
    )
    assert status == 200, body
    assert body["status"] == "granted", body

    # The cache may still be serving the just-invalidated denial (the
    # asymmetry the module docstring notes — grants aren't event-invalidated,
    # only bounded by the same TTL), so this also polls rather than asserting.
    assert _poll_until(lambda: _kenji_can_read_reviews(kenji_token)), "kenji's access must be fully restored"


def test_editor_cannot_revoke_or_grant_access(jdoe_token: str) -> None:
    """jdoe is editor-only, never admin — separation of duties, same
    pattern as relation-type creation and Action approval.
    """
    status, body = _request(
        "POST", f"{IDENTITY}/principals/{KENJI_URN}/access/revoke", token=jdoe_token, body={"relation": "viewer"}
    )
    assert status == 403, body

    status, body = _request(
        "POST", f"{IDENTITY}/principals/{KENJI_URN}/access/grant", token=jdoe_token, body={"relation": "viewer"}
    )
    assert status == 403, body


def test_invalid_relation_is_rejected(msmith_token: str) -> None:
    status, body = _request(
        "POST", f"{IDENTITY}/principals/{KENJI_URN}/access/revoke", token=msmith_token, body={"relation": "owner"}
    )
    assert status == 400, body
    assert "relation" in body["detail"], body


def test_grant_to_unknown_principal_is_rejected(msmith_token: str) -> None:
    unknown_urn = f"hl:{TENANT_ID}:global:user:does-not-exist"
    status, body = _request(
        "POST",
        f"{IDENTITY}/principals/{unknown_urn}/access/grant",
        token=msmith_token,
        body={"relation": "viewer"},
    )
    assert status == 404, body
    assert "unknown principal" in body["detail"], body
