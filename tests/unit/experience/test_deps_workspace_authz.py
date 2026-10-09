"""Experience deps `_authorize_workspace` mirrors the holon_common authz client."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("HOLON_TENANT_ID", "acme")
os.environ.setdefault("HOLON_WORKSPACE_ID", "main")
os.environ.setdefault("HOLON_JWT_SECRET", "unit-test-jwt-secret-must-be-long")
os.environ.setdefault("HOLON_DB_URL", "postgresql://holon:holon@localhost:5432/holon_experience")
os.environ.setdefault("HOLON_KAFKA_BOOTSTRAP", "localhost:9092")
os.environ.setdefault("HOLON_IDENTITY_URL", "http://localhost:8001")
os.environ.setdefault("HOLON_CONNECTIVITY_URL", "http://localhost:8002")
os.environ.setdefault("HOLON_KNOWLEDGE_URL", "http://localhost:8003")
os.environ.setdefault("HOLON_INTELLIGENCE_URL", "http://localhost:8004")
os.environ.setdefault("HOLON_AUTOMATION_URL", "http://localhost:8005")
os.environ.setdefault("HOLON_SPICEDB_URL", "http://localhost:8443")
os.environ.setdefault("HOLON_SPICEDB_PRESHARED_KEY", "change-me")
os.environ.setdefault("HOLON_OPA_URL", "http://localhost:8181")

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "experience"))

from holon_common import HolonError, Principal  # noqa: E402

from app import deps  # noqa: E402


def _principal() -> Principal:
    return Principal(
        urn="hl:acme:global:user:jdoe",
        type="user",
        tenant_id="acme",
        display_name="Jane",
    )


def test_authorize_workspace_allows() -> None:
    deps.authz = SimpleNamespace(
        authorize=AsyncMock(return_value=SimpleNamespace(allowed=True, reason="ok"))
    )
    asyncio.run(deps._authorize_workspace(_principal(), "read"))
    deps.authz.authorize.assert_awaited_once()


def test_authorize_workspace_forbidden() -> None:
    deps.authz = SimpleNamespace(
        authorize=AsyncMock(return_value=SimpleNamespace(allowed=False, reason="nope"))
    )
    with pytest.raises(HolonError) as exc:
        asyncio.run(deps._authorize_workspace(_principal(), "approve"))
    assert exc.value.status_code == 403
