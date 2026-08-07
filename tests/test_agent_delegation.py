"""Agent delegation: "Un agent est un Principal avec identité
propre... ses droits effectifs sont l'intersection de ses droits propres
et de ceux de son mandant. Il ne peut jamais dépasser son mandant."

The seeded agent `ingest-bot` (workspace viewer) acts on behalf of jdoe
(workspace editor). These tests pin the two directions of the intersection:

- the intersection never *widens* past the agent's own grant (a viewer
  agent cannot write even though its editor mandant can);
- it never widens past the mandant's grant either (revoke the mandant and
  the agent is denied even while its own grant is untouched).

Black-box over HTTP, same style as the other test modules. Requires the
stack running (`make up`).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import IDENTITY, KNOWLEDGE, TENANT_ID, _request


JDOE_URN = f"hl:{TENANT_ID}:global:user:jdoe"
AGENT_URN = f"hl:{TENANT_ID}:global:agent:ingest-bot"
ADMIN_URN = f"hl:{TENANT_ID}:global:user:msmith"


def _token_for(principal_urn: str) -> str:
    deadline = time.monotonic() + 60
    local_name = principal_urn.rsplit(":", 1)[-1]
    while time.monotonic() < deadline:
        status, body = _request(
            "POST", f"{IDENTITY}/token",
            body={"principal_urn": principal_urn, "client_secret": f"{local_name}-dev-secret"},
        )
        if status == 200:
            return body["access_token"]
        time.sleep(1.5)
    pytest.fail(f"could not mint a token for {principal_urn}")


def _poll_until(predicate, *, timeout: float = 10.0) -> bool:
    """Decision cache means a revoke/grant isn't visible on the
    literal next request anymore — it propagates via
    `identity.permission.revoked` (revokes) or the cache's own 5s TTL
    (grants, not event-invalidated). Poll for convergence rather than asserting immediately, same
    idiom used throughout this suite for other genuinely-async effects.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.5)
    return predicate()


@pytest.fixture(scope="session")
def agent_token() -> str:
    return _token_for(AGENT_URN)


@pytest.fixture(scope="session")
def admin_token() -> str:
    return _token_for(ADMIN_URN)


@pytest.fixture(scope="session")
def jdoe_token() -> str:
    return _token_for(JDOE_URN)


def test_agent_reads_within_the_intersection(agent_token: str) -> None:
    """Agent (viewer) on behalf of jdoe (editor): read is in both grants."""
    status, body = _request("GET", f"{KNOWLEDGE}/objects/Customer", token=agent_token)
    assert status == 200, body


def test_agent_cannot_exceed_its_own_grant(agent_token: str) -> None:
    """The mandant being an editor never *widens* the viewer agent's rights:
    invoking an Action (write) is denied on the agent's own grant."""
    status, body = _request(
        "POST",
        f"{KNOWLEDGE}/objects/Customer/1/actions/putOnCreditHold",
        token=agent_token,
        body={"reason": "delegation test"},
    )
    assert status == 403, body
    assert "rebac_denied" in body["detail"], body


def test_agent_cannot_exceed_its_mandant(agent_token: str, admin_token: str, jdoe_token: str) -> None:
    """Revoke the mandant (jdoe) entirely — viewer and editor. The agent's
    own viewer grant is deliberately left untouched: any denial afterwards
    can only come from the mandant side of the intersection. Self-cleaning.
    """
    for relation in ("viewer", "editor"):
        status, body = _request(
            "POST", f"{IDENTITY}/principals/{JDOE_URN}/access/revoke",
            token=admin_token, body={"relation": relation},
        )
        assert status == 200, body
    try:
        assert _poll_until(
            lambda: _request("GET", f"{KNOWLEDGE}/objects/Customer", token=agent_token)[0] == 403
        ), "agent denial never propagated within budget"
        status, body = _request("GET", f"{KNOWLEDGE}/objects/Customer", token=agent_token)
        assert status == 403, body
        assert "on behalf of" in body["detail"], body

        # sanity: the mandant herself is denied too, not just the agent
        status, body = _request("GET", f"{KNOWLEDGE}/objects/Customer", token=jdoe_token)
        assert status == 403, body
    finally:
        for relation in ("viewer", "editor"):
            status, body = _request(
                "POST", f"{IDENTITY}/principals/{JDOE_URN}/access/grant",
                token=admin_token, body={"relation": relation},
            )
            assert status == 200, body

    # restored: both read again — the test leaves the stack as it found it.
    # Grants aren't event-invalidated (module docstring's asymmetry), so
    # this polls the cache's TTL out rather than asserting immediately.
    assert _poll_until(
        lambda: _request("GET", f"{KNOWLEDGE}/objects/Customer", token=jdoe_token)[0] == 200
    ), "mandant restoration never converged"
    assert _poll_until(
        lambda: _request("GET", f"{KNOWLEDGE}/objects/Customer", token=agent_token)[0] == 200
    ), "agent restoration never converged"
