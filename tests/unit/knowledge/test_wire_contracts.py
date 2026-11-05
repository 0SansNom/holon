"""Wire, masking-adjacent pure helpers, and the small IO wrappers.

``app.api`` and ``app.actions`` package barrels pull the whole service
(DuckDB, Iceberg). These tests load the leaf modules through a package
shell so the barrel does not run.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import types
from pathlib import Path

import pytest
from holon_common.auth import Principal
from holon_common.correlation import set_correlation_id
from holon_common.errors import HolonError

_REPO = Path(__file__).resolve().parents[3]
_APP = _REPO / "services" / "knowledge" / "app"


def _shell(package: str, directory: Path) -> None:
    if package in sys.modules and getattr(sys.modules[package], "__file__", None):
        return
    module = types.ModuleType(package)
    module.__path__ = [str(directory)]
    module.__package__ = package
    sys.modules[package] = module


def _load(package: str, directory: Path, name: str):
    _shell(package, directory)
    return importlib.import_module(f"{package}.{name}")


def _load_metered(package: str, directory: Path, name: str, series: str):
    """Import a module that registers Prometheus series once per process.

    The root conftest drops ``app.*`` between files. A second import would
    register the same series again, which Prometheus rejects.
    """
    from prometheus_client import REGISTRY

    if series in REGISTRY._names_to_collectors:
        return sys.modules.get(f"{package}.{name}")
    return _load(package, directory, name)


wire = _load("app.actions", _APP / "actions", "wire")
notify = _load("app.actions", _APP / "actions", "notify")
hardcoded = _load("app.actions", _APP / "actions", "hardcoded")
_load_metered("app.actions", _APP / "actions", "metrics", "holon_action_events")
object_wire = _load("app.api", _APP / "api", "object_wire")
public_only = _load("app.api", _APP / "api", "public_only")

from app import catalog_errors, catalog_state, glossary, query_log, search_constants, struct_values  # noqa: E402

_load_metered("app", _APP, "search_metrics", "holon_search_reindex_failed_object_types")


def _run(coro):
    return asyncio.run(coro)


def test_struct_projection_parse_and_assemble():
    rule = {"kind": "struct", "properties": {"city": {}, "zip": {"column": "zip_code"}}}
    assert struct_values.project_struct_to_declared_fields({"properties": {}}, {"a": 1}) == {"a": 1}
    assert struct_values.project_struct_to_declared_fields(rule, {"city": "Paris", "extra": 1}) == {"city": "Paris"}
    assert struct_values.parse_struct_or_array(rule, None) is None
    assert struct_values.parse_struct_or_array(rule, {"city": "Paris", "extra": 1}) == {"city": "Paris"}
    assert struct_values.parse_struct_or_array({"kind": "array"}, [1, 2]) == [1, 2]
    assert struct_values.parse_struct_or_array({"kind": "struct"}, [1]) == [1]
    assert struct_values.parse_struct_or_array(rule, 3) == 3
    assert struct_values.parse_struct_or_array(rule, "{") == "{"
    assert struct_values.parse_struct_or_array(rule, '{"city": "Lyon", "extra": 1}') == {"city": "Lyon"}
    assert struct_values.parse_struct_or_array({"kind": "array"}, "[1]") == [1]
    assert struct_values.parse_struct_or_array({"kind": "array"}, '{"a": 1}') == '{"a": 1}'

    assert struct_values.assemble_struct_value(rule, {"zip_code": "75001"}, None) == {"zip": "75001"}
    assert struct_values.assemble_struct_value(rule, {"address": '{"city": "Paris"}', "zip_code": "75001"}, "address") == {
        "city": "Paris",
        "zip": "75001",
    }
    assert struct_values.assemble_struct_value(rule, {"address": "[1]", "zip_code": None}, "address") is None
    assert struct_values.assemble_struct_value(
        {"kind": "struct", "properties": {"city": "nope", "zip": {"column": ""}}},
        {},
        "missing",
    ) is None


def test_object_wire_and_public_surface():
    row = object_wire.enrich_object_row(
        {"id": 7, "__apiName": "Kept"}, object_type="Customer", tenant_id="acme", workspace_id="main"
    )
    assert row["__apiName"] == "Kept"
    assert row["__primaryKey"] == 7
    assert row["__rid"] == "hl:acme:main:object:Customer/7"
    kept = object_wire.enrich_object_row(
        {"id": 1, "__rid": "already"}, object_type="Customer", tenant_id="acme", workspace_id="main"
    )
    assert kept["__rid"] == "already"
    assert "__rid" not in object_wire.enrich_object_row(
        {}, object_type="Customer", tenant_id="acme", workspace_id="main"
    )
    page = object_wire.enrich_page(
        {"data": [{"id": 1}]}, object_type="Customer", tenant_id="acme", workspace_id="main"
    )
    assert page["data"][0]["__primaryKey"] == 1
    empty = object_wire.enrich_page({}, object_type="Customer", tenant_id="acme", workspace_id="main")
    assert empty["data"] == []

    sent: list = []

    async def app(scope, receive, send):
        sent.append("app")

    async def send(message):
        sent.append(message)

    middleware = public_only.PublicApiOnlyMiddleware(app)

    async def call(path, typ="http"):
        sent.clear()
        await middleware({"type": typ, "path": path}, None, send)

    _run(call("/health"))
    assert sent == ["app"]
    _run(call("/api/holon/search"))
    assert sent == ["app"]
    _run(call("/docs/extra"))
    assert sent == ["app"]
    _run(call("/secret", typ="websocket"))
    assert sent == ["app"]
    _run(call("/internal"))
    assert sent[0]["status"] == 404
    assert b"PathNotFound" in sent[1]["body"]
    assert public_only._allowed("/metrics") is True
    assert public_only._allowed("/nope") is False


def test_action_wire_notify_and_event():
    assert wire.validation_report({"result": "ok"}) == {
        "result": "ok", "parameters": {}, "submissionCriteriaResult": None, "messages": [],
    }
    assert wire.operation_id("acme", "main", {"invocationId": 4}).endswith("action-invocation:4")
    assert wire.operation_id("acme", "main", {"approvalId": 9}).endswith("action-approval:9")
    assert wire.operation_id("acme", "main", {}) is None
    assert wire.resolve_target({"target_object_type": "Customer"}, object_type=None, primary_key=3) == ("Customer", "3")
    with pytest.raises(HolonError):
        wire.resolve_target({}, object_type=None, primary_key=None)
    body = wire.success_envelope(
        tenant_id="acme", workspace_id="main", object_type="Customer", primary_key="3",
        apply_result={"invocationId": 4, "edited": True},
    )
    assert body["operationId"].endswith(":4")
    assert body["target"] == {"objectType": "Customer", "primaryKey": "3"}
    bare = wire.success_envelope(
        tenant_id="acme", workspace_id="main", object_type="Customer", primary_key="3",
        apply_result={"edited": False},
    )
    assert "operationId" not in bare

    class _Response:
        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *args, **kwargs):
            self.posts = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            self.posts.append((url, json))
            return _Response()

    holder: dict = {}

    def _factory(*args, **kwargs):
        holder["client"] = _Client()
        return holder["client"]

    notify.httpx.AsyncClient = _factory
    _run(notify.deliver_action_notification(webhook_url=None, event="x", payload={}))
    _run(notify.deliver_action_notification(webhook_url="  ", event="x", payload={}))
    _run(notify.deliver_action_notification(webhook_url=" http://hook ", event="applied", payload={"id": 1}))
    assert holder["client"].posts[0][0] == "http://hook"
    assert holder["client"].posts[0][1]["event"] == "applied"

    class _Boom(_Client):
        async def post(self, url, json):
            raise RuntimeError("down")

    notify.httpx.AsyncClient = lambda *a, **k: _Boom()
    import os
    os.environ["HOLON_ACTION_NOTIFY_WEBHOOK"] = "http://env-hook"
    try:
        _run(notify.deliver_action_notification(webhook_url="  ", event="applied", payload={"id": 1}))
    finally:
        os.environ.pop("HOLON_ACTION_NOTIFY_WEBHOOK", None)

    actor = Principal(urn="hl:acme:global:user:jdoe", type="user", tenant_id="acme", display_name="Jane")
    set_correlation_id("corr-1")
    event = hardcoded._event(
        event_type="knowledge.object.updated",
        tenant_id="acme",
        workspace_id="main",
        instance_urn="hl:acme:main:object:Customer/1",
        actor=actor,
        payload={"k": 1},
    )
    assert event.correlation_id == "corr-1"
    assert event.actor.urn.endswith("jdoe")
    assert event.producer == "knowledge-platform@0.1.0"
    set_correlation_id(None)
    fresh = hardcoded._event(
        event_type="knowledge.object.updated",
        tenant_id="acme",
        workspace_id="main",
        instance_urn="hl:acme:main:object:Customer/1",
        actor=actor,
        payload={},
    )
    assert fresh.correlation_id == fresh.event_id
    from prometheus_client import REGISTRY

    action_events = REGISTRY._names_to_collectors["holon_action_events"]
    assert action_events._name.startswith("holon_action_events")


class _Pool:
    def __init__(self, row):
        self.row = row
        self.executed = []

    async def execute(self, *args):
        self.executed.append(args)

    async def fetch(self, *args):
        return [self.row] if self.row else []

    async def fetchrow(self, *args):
        return self.row


def test_glossary_and_query_log_use_the_pool():
    row = {"term": "client", "definition": "A customer", "synonyms": ["customer"], "related_object_type_urn": None}
    pool = _Pool(row)
    assert _run(glossary.list_terms(pool, "acme")) == [row]
    assert _run(glossary.get_term(pool, "acme", "client")) == row
    created = _run(glossary.create_term(
        pool, tenant_id="acme", term="client", definition="A customer", synonyms=["customer"], related_object_type_urn=None
    ))
    assert created == row
    assert pool.executed

    missing = _Pool(None)
    assert _run(glossary.get_term(missing, "acme", "nope")) is None
    assert _run(glossary.list_terms(missing, "acme")) == []

    log = _Pool({"id": 1, "query_text": "acme", "result_count": 2, "executed_at": None})
    _run(query_log.record_query(log, "acme", "acme", 2))
    assert _run(query_log.list_recent(log, "acme"))[0]["query_text"] == "acme"
    assert log.executed[0][1:] == ("acme", "acme", 2)


def test_catalog_and_search_constants_import():
    with pytest.raises(catalog_errors.TransientCatalogError):
        raise catalog_errors.TransientCatalogError("retry")
    assert catalog_state.join_link_backfill_status == "idle"
    assert catalog_state.search_reindex_status["state"] == "idle"
    assert search_constants.INDEX_NAME == "holon-search"
    assert search_constants.POLICY_VERSION == 2
    from prometheus_client import REGISTRY

    assert "holon_search_reindex_failed_object_types" in REGISTRY._names_to_collectors
    assert logging.getLogger("knowledge.actions.notify")
