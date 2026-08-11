"""Shared test harness — every integration test in this suite talks to
the real, running docker-compose stack over plain HTTP (no mocking),
and until now each of the ~60 test files hand-copied the same
`_request`/`_token_for`/`_unique_name` helpers and the same four seeded-
principal token fixtures. Extracted here — real integration tests
against the live stack catch cross-service bugs a mocked test would
miss, which is why this suite is built this way rather than mocking
each service's dependencies.

`_request`'s default `timeout=30` is deliberately the most generous
value any single test file used standalone (some used 15/20/60/180) —
raising it never breaks a test that used to pass (client timeout is
just a ceiling, not something under test), only avoids spuriously
failing a slow-but-healthy call. A test needing a shorter or longer
ceiling than the default can still pass `timeout=` explicitly.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

IDENTITY = "http://localhost:8001"
CONNECTIVITY = "http://localhost:8002"
KNOWLEDGE = "http://localhost:8003"
EXPERIENCE = "http://localhost:8004"
AUTOMATION = "http://localhost:8005"
INTELLIGENCE = "http://localhost:8006"
OPENSEARCH = "http://localhost:9200"

TENANT_ID = "acme"


def _request(method: str, url: str, *, token: str | None = None, body: dict | None = None, timeout: float = 30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _token_for(principal_urn: str, *, deadline_seconds: float = 60) -> str:
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        local_name = principal_urn.rsplit(":", 1)[-1]
        status, body = _request(
            "POST", f"{IDENTITY}/token",
            body={"principal_urn": principal_urn, "client_secret": f"{local_name}-dev-secret"},
        )
        if status == 200:
            return body["access_token"]
        time.sleep(1.5)
    pytest.fail(f"could not mint a token for {principal_urn}")


def _unique_name(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}"


@pytest.fixture(scope="session")
def jdoe_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:jdoe")


@pytest.fixture(scope="session")
def msmith_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:msmith")


@pytest.fixture(scope="session")
def kenji_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:kenji")


@pytest.fixture(scope="session")
def alice_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:alice")
