"""`use_tls` survives the whole path: request model, stored connection, and the driver call."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.modules.setdefault("asyncpg", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app import sql_drivers, sql_source_fetch, sql_source_registry  # noqa: E402
from app.ingest_models import RegisterSqlConnectionRequest  # noqa: E402

pytestmark = pytest.mark.unit


class _RegisterPool:
    def __init__(self) -> None:
        self.inserted: tuple = ()

    async def fetchrow(self, sql: str, *args):
        if sql.lstrip().startswith("SELECT host"):
            return None
        return {"use_tls": self.inserted[10] if self.inserted else None}

    async def execute(self, sql: str, *args):
        self.inserted = args


@pytest.mark.parametrize(("dialect", "requested", "stored"), [("alloydb", None, True), ("postgres", None, False), ("postgres", True, True)])
def test_register_stores_the_resolved_tls_choice(monkeypatch, dialect, requested, stored) -> None:
    monkeypatch.setattr(sql_source_registry, "assert_connector_host", lambda host: None)
    pool = _RegisterPool()

    connection = asyncio.run(
        sql_source_registry.register_connection(
            pool,
            tenant_id="acme",
            name="warehouse",
            host="db.example.com",
            database="sales",
            username="reader",
            created_by_urn="hl:acme:global:user:admin",
            dialect=dialect,
            secret_ref="env:HOLON_CONN_ACME__WAREHOUSE",
            use_tls=requested,
        )
    )

    assert pool.inserted[10] is stored
    assert connection["use_tls"] is stored


def test_request_model_accepts_use_tls() -> None:
    body = RegisterSqlConnectionRequest(name="w", host="h", database="d", username="u", use_tls=True)
    assert body.use_tls is True


class _FetchPool:
    async def fetchrow(self, sql: str, *args):
        if "FROM sql_source" in sql:
            return {
                "connection_name": "warehouse",
                "table_name": "orders",
                "query": None,
                "cursor_property": None,
                "last_cursor_value": None,
                "cursor_boundary_keys": None,
            }
        return {
            "dialect": "postgres",
            "host": "db.example.com",
            "port": 5432,
            "database": "sales",
            "warehouse": None,
            "username": "reader",
            "password": None,
            "secret_ref": "env:HOLON_CONN_ACME__WAREHOUSE",
            "use_tls": True,
        }


def test_fetch_passes_the_stored_tls_choice_to_the_driver(monkeypatch) -> None:
    calls: list[dict] = []

    async def fetch_dicts(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(sql_source_fetch, "pin_connector_host", lambda host: host)
    monkeypatch.setattr(sql_source_fetch, "resolve_source_secret", lambda ref, tenant_id: "s3cret")
    monkeypatch.setattr(sql_source_fetch, "connector_secret", lambda **kwargs: "s3cret")
    monkeypatch.setattr(sql_drivers, "fetch_dicts", fetch_dicts)

    asyncio.run(sql_source_fetch.fetch_for_dataset(_FetchPool(), "acme", "orders"))

    assert calls[0]["use_tls"] is True
