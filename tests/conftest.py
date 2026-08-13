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
import os
import sys
import time
import urllib.error
import urllib.request

import pytest

# Unit/integration helpers that call issue_token for users (walking_skeleton,
# auth unit tests) need this; production services must not set it.
os.environ.setdefault("HOLON_ALLOW_LOCAL_USER_MINT", "1")

IDENTITY = "http://localhost:8001"
CONNECTIVITY = "http://localhost:8002"
KNOWLEDGE = "http://localhost:8003"
EXPERIENCE = "http://localhost:8004"
AUTOMATION = "http://localhost:8005"
INTELLIGENCE = "http://localhost:8006"
OPENSEARCH = "http://localhost:9200"

TENANT_ID = "acme"


def as_items(body):
    """Normalize collection responses to a list of instances.

    List/link/interface reads now return `{items, next_cursor, page_size}`
    (links also keep relation metadata). Legacy bare arrays still work.
    """
    if isinstance(body, list):
        return body
    if isinstance(body, dict) and "items" in body:
        return body["items"]
    return body


def _is_pure_object_page(body) -> bool:
    return (
        isinstance(body, dict)
        and "items" in body
        and "next_cursor" in body
        and "page_size" in body
        and "relation" not in body
    )


def _request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict | None = None,
    timeout: float = 30,
    unwrap_pages: bool = True,
):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status, payload = response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, json.loads(exc.read())
    if unwrap_pages and _is_pure_object_page(payload):
        return status, payload["items"]
    return status, payload


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


def _clear_app_modules() -> None:
    for key in [key for key in sys.modules if key == "app" or key.startswith("app.")]:
        del sys.modules[key]


# Per-file snapshot of whatever `app`/`app.*` stubs that file's own module-level
# code left in `sys.modules` right after it was imported/collected.
_app_module_snapshots: dict = {}


def pytest_collectstart(collector) -> None:
    """Several test files stub `sys.modules["app"...]` with lightweight fakes
    to unit-test pure ontology logic without importing this service's full
    dependency chain (asyncpg/httpx/...). Those stubs are hand-built per file
    and only implement what that one file needs — none of them clean up
    after themselves, so whichever file's stub happens to import (i.e.
    *collect*, since the stub-building runs at module scope) first
    permanently shadows the real module for every file collected after it in
    the same process (surfaces as `ImportError: cannot import name X from
    'app.ontology.Y' (unknown location)` in an unrelated file). Clearing
    `app`/`app.*` out of `sys.modules` before each file is collected gives
    every file a clean slate regardless of collection order.
    """
    _clear_app_modules()


def pytest_itemcollected(item) -> None:
    """Collection fully finishes for every file before any test runs — so by
    execution time, only the *last*-collected file's stubs are still in
    `sys.modules`. Some tests also reach into `sys.modules["app...]` from
    inside the test function itself (not just at module scope), expecting
    whatever their own file's module-level code set up there. Snapshot each
    file's post-import `app.*` state once, right after it's collected, so
    `pytest_runtest_setup` below can restore exactly that file's state before
    any of its tests run — independent of collection order.
    """
    path = item.path
    if path not in _app_module_snapshots:
        _app_module_snapshots[path] = {
            key: module for key, module in sys.modules.items() if key == "app" or key.startswith("app.")
        }


def pytest_runtest_setup(item) -> None:
    snapshot = _app_module_snapshots.get(item.path)
    if snapshot is not None:
        _clear_app_modules()
        sys.modules.update(snapshot)


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
