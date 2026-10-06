"""One correlation id follows an action through HTTP, Kafka and the audit trail."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "libs"))

from holon_common import audit, correlation, events  # noqa: E402
from holon_common.audit_store import list_events  # noqa: E402
from holon_common.correlation import HEADER, current_correlation_id, instrument_correlation  # noqa: E402


def _app() -> TestClient:
    app = FastAPI()
    instrument_correlation(app)

    @app.get("/whoami")
    async def whoami() -> dict:
        return {"correlationId": current_correlation_id()}

    return TestClient(app)


def test_inbound_id_is_kept_and_echoed() -> None:
    response = _app().get("/whoami", headers={HEADER: "action-42"})
    assert response.json() == {"correlationId": "action-42"}
    assert response.headers[HEADER] == "action-42"


def test_missing_or_invalid_id_gets_a_fresh_one() -> None:
    client = _app()
    generated = client.get("/whoami").json()["correlationId"]
    replaced = client.get("/whoami", headers={HEADER: "bad id\n"}).json()["correlationId"]
    assert generated and len(generated) == 32
    assert replaced and replaced != "bad id\n"
    assert current_correlation_id() is None


def test_outbound_httpx_calls_forward_the_current_id() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return httpx.Response(200)

    async def call(explicit: dict) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await client.get("http://knowledge/x", headers=explicit)

    async def run() -> None:
        correlation.set_correlation_id("action-7")
        await call({})
        await call({HEADER: "upstream-chosen"})
        correlation.set_correlation_id(None)
        await call({})

    asyncio.run(run())
    assert seen[0][HEADER.lower()] == "action-7"
    assert seen[1][HEADER.lower()] == "upstream-chosen"
    assert HEADER.lower() not in seen[2]


def test_audit_records_take_the_current_id_as_trace_id() -> None:
    async def run() -> tuple[dict, dict]:
        correlation.set_correlation_id("action-9")
        implicit = audit.build_audit_record(category="access", action="read", outcome="allow")
        explicit = audit.build_audit_record(category="access", action="read", outcome="allow", trace_id="otel-1")
        return implicit, explicit

    implicit, explicit = asyncio.run(run())
    assert implicit["traceId"] == "action-9"
    assert explicit["traceId"] == "otel-1"


def test_consumer_restores_the_event_correlation_id(monkeypatch) -> None:
    envelope = {
        "event_type": "identity.token.revoked",
        "tenant_id": "acme",
        "aggregate_type": "Token",
        "aggregate_id": "jti-1",
        "correlation_id": "action-11",
        "partition_key": "acme/jti-1",
        "producer": "identity",
        "actor": {"type": "user", "urn": "hl:acme:global:user:jdoe"},
        "payload": {},
    }

    class _Messages:
        def __aiter__(self):
            async def gen():
                yield SimpleNamespace(value=envelope)

            return gen()

    monkeypatch.setattr(events.registry, "validate", lambda *args: None)
    consumer = events.EventConsumer("kafka:9092", ["identity"], "test")
    consumer._consumer = _Messages()

    async def run() -> list[str | None]:
        seen = []
        async for _event in consumer:
            seen.append(current_correlation_id())
        return seen

    assert asyncio.run(run()) == ["action-11"]


def test_audit_store_filters_by_trace_id() -> None:
    captured: dict = {}

    class _Pool:
        async def fetch(self, sql: str, *args):
            captured["sql"], captured["args"] = sql, args
            return []

    asyncio.run(list_events(_Pool(), "acme", trace_id="action-13", page_size=10))
    assert "trace_id = $2" in captured["sql"]
    assert captured["args"] == ("acme", "action-13", 10)
