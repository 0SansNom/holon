"""Shared pytest fixtures for Holon.

Stack helpers (`jdoe_token`, `ontology_url`, …) are for integration tests.
Unit tests under ``tests/unit/`` should not need the compose stack.

Markers (see ``pytest.ini``):
- ``unit`` — no live stack
- ``integration`` — compose HTTP / local infra
- ``llm`` — real LLM spend (excluded from default CI)
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

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
WORKSPACE_ID = "main"


def pytest_collection_modifyitems(config, items) -> None:
    """Auto-mark by path: ``tests/unit/`` → unit, else integration (unless llm-only)."""
    for item in items:
        path = Path(str(getattr(item, "path", item.fspath)))
        if "unit" in path.parts:
            item.add_marker(pytest.mark.unit)
            continue
        markers = {m.name for m in item.iter_markers()}
        if "llm" in markers:
            # LLM suite is its own slice; still integration in practice.
            item.add_marker(pytest.mark.integration)
            continue
        if "unit" not in markers:
            item.add_marker(pytest.mark.integration)


def ontology_url(path: str = "") -> str:
    """Knowledge Ontology surface: `/api/ontologies/{workspace}/…`."""
    suffix = path if path.startswith("/") else f"/{path}" if path else ""
    return f"{KNOWLEDGE}/api/ontologies/{WORKSPACE_ID}{suffix}"


def holon_url(path: str = "") -> str:
    """Knowledge Holon-native surface: `/api/holon/…`."""
    suffix = path if path.startswith("/") else f"/{path}" if path else ""
    return f"{KNOWLEDGE}/api/holon{suffix}"


def resync_and_wait_for_instance(
    *,
    token: str,
    dataset: str,
    object_type: str,
    instance_id: str = "1",
    timeout: float = 30,
) -> dict:
    """Re-sync so catalog materializes the ObjectType, then wait for a serving-store row."""
    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=token, body={"dataset": dataset})
    assert status == 200, result
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        status, body = _request("GET", ontology_url(f"/objects/{object_type}/{instance_id}"), token=token)
        last = (status, body)
        if status == 200:
            return body
        time.sleep(1)
    pytest.fail(f"serving store never materialized {object_type}/{instance_id}: {last}")


def as_items(body):
    """Normalize collection responses to a list of instances."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        if "data" in body and isinstance(body["data"], list):
            return body["data"]
        if "items" in body:
            return body["items"]
    return body


def _is_pure_object_page(body) -> bool:
    return (
        isinstance(body, dict)
        and "data" in body
        and "nextPageToken" in body
        and "pageSize" in body
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
        return status, payload["data"]
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


def _clear_magicmock_third_party() -> None:
    """Drop stand-in modules so a later file can import the real package.

    White-box tests plant ``MagicMock()`` or empty ``types.ModuleType`` under
    names like ``httpx``. Those survive across collection and break FastAPI
    TestClient (needs a real ``httpx.Response``).
    """
    from unittest.mock import MagicMock

    for name in ("httpx", "asyncpg", "anthropic", "joblib", "httpcore"):
        mod = sys.modules.get(name)
        if mod is None:
            continue
        if isinstance(mod, MagicMock) or getattr(mod, "__file__", None) is None:
            del sys.modules[name]


# Per-file snapshot of whatever `app`/`app.*` stubs that file's own module-level
# code left in `sys.modules` right after it was imported/collected.
_app_module_snapshots: dict = {}


def pytest_collectstart(collector) -> None:
    """Several test files stub `sys.modules["app"...]` with lightweight fakes."""
    _clear_app_modules()
    _clear_magicmock_third_party()


def pytest_itemcollected(item) -> None:
    """Collection fully finishes for every file before any test runs."""
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
