"""Unit tests for Connectivity ReBAC helpers."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

# Connectivity deps reads env at import time.
os.environ.setdefault("HOLON_TENANT_ID", "acme")
os.environ.setdefault("HOLON_WORKSPACE_ID", "main")
os.environ.setdefault("HOLON_JWT_SECRET", "unit-test-secret")
os.environ.setdefault("HOLON_DB_URL", "postgresql://holon:holon@localhost:5432/holon_connectivity")
os.environ.setdefault("HOLON_KNOWLEDGE_URL", "http://localhost:8003")
os.environ.setdefault("HOLON_KAFKA_BOOTSTRAP", "localhost:9092")
os.environ.setdefault("HOLON_SPICEDB_URL", "http://localhost:8443")
os.environ.setdefault("HOLON_SPICEDB_PRESHARED_KEY", "change-me")
os.environ.setdefault("HOLON_OPA_URL", "http://localhost:8181")
os.environ.setdefault("HOLON_ICEBERG_CATALOG_URI", "http://localhost:8181")
os.environ.setdefault("HOLON_ICEBERG_WAREHOUSE", "s3://holon-warehouse/")
os.environ.setdefault("HOLON_S3_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "holon")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "holon")
os.environ.setdefault("AWS_REGION", "us-east-1")

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "libs"))
sys.path.insert(0, str(ROOT / "services" / "connectivity"))

from holon_common import HolonError  # noqa: E402

from app import authz_seed, deps  # noqa: E402
from app.deps import pipeline_urn, source_urn, workspace_urn  # noqa: E402
from app.routers import sources  # noqa: E402


def test_source_urn_includes_workspace_segment() -> None:
    assert source_urn("acme", "main", "reviews") == "hl:acme:main:source:reviews"


def test_pipeline_urn_uses_workspace_not_global() -> None:
    assert pipeline_urn("acme", "main", "enrich") == "hl:acme:main:pipeline:enrich"
    assert ":global:pipeline:" not in pipeline_urn("acme", "main", "enrich")


def test_workspace_urn_shape() -> None:
    assert workspace_urn("acme", "main") == "hl:acme:global:workspace:main"


class _Principal:
    tenant_id = "acme"


class _Registry:
    def __init__(self, source):
        self.source = source

    async def get_source(self, pool, tenant_id, name):
        return self.source


def _failing_seed(monkeypatch) -> None:
    async def _fail(*args, **kwargs):
        raise RuntimeError("spicedb down")

    monkeypatch.setattr(authz_seed, "seed_source_parent_workspace", _fail)


def test_seed_failure_compensates_new_row(monkeypatch) -> None:
    _failing_seed(monkeypatch)
    deleted = []

    async def _compensate():
        deleted.append(True)

    with pytest.raises(HolonError) as exc:
        asyncio.run(
            deps._seed_source_authz(
                tenant_id="acme", workspace_id="main", name="s", compensate_delete=_compensate
            )
        )
    assert exc.value.error_name == "AuthzSeedFailed"
    assert deleted == [True]


def test_seed_failure_keeps_preexisting_row(monkeypatch) -> None:
    _failing_seed(monkeypatch)
    with pytest.raises(HolonError):
        asyncio.run(
            deps._seed_source_authz(
                tenant_id="acme", workspace_id="main", name="s", compensate_delete=None
            )
        )


def test_source_update_requires_write_on_existing_source(monkeypatch) -> None:
    checked = []

    async def _authorize(principal, permission, *, name, workspace_id=None):
        checked.append((permission, name, workspace_id))

    monkeypatch.setattr(sources, "_authorize_source", _authorize)
    existing = asyncio.run(
        sources._authorize_source_update(
            _Registry({"name": "s", "workspace_id": "main"}), _Principal(), "s", "main"
        )
    )
    assert existing is not None
    assert checked == [("write", "s", "main")]


def test_source_update_rejects_workspace_move(monkeypatch) -> None:
    async def _authorize(*args, **kwargs):
        return None

    monkeypatch.setattr(sources, "_authorize_source", _authorize)
    with pytest.raises(HolonError) as exc:
        asyncio.run(
            sources._authorize_source_update(
                _Registry({"name": "s", "workspace_id": "main"}), _Principal(), "s", "other"
            )
        )
    assert exc.value.error_name == "SourceWorkspaceMismatch"


def test_new_source_skips_source_authz(monkeypatch) -> None:
    async def _authorize(*args, **kwargs):
        raise AssertionError("no source row to authorize against")

    monkeypatch.setattr(sources, "_authorize_source", _authorize)
    assert asyncio.run(
        sources._authorize_source_update(_Registry(None), _Principal(), "s", "main")
    ) is None
