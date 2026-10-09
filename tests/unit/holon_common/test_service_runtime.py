"""Unit tests for shared FastAPI lifespan helpers."""

from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock, patch

from holon_common.service_runtime import (
    list_audit_events_http,
    reset_durable_audit,
    wire_authz,
)


def test_reset_durable_audit_clears_then_installs() -> None:
    pool = object()
    with (
        patch("holon_common.service_runtime.clear_durable_audit_hooks") as clear,
        patch("holon_common.service_runtime.install_durable_audit") as install,
    ):
        reset_durable_audit(pool)
    clear.assert_called_once_with()
    install.assert_called_once_with(pool)


def test_wire_authz_sets_app_state_and_deps() -> None:
    app = types.SimpleNamespace(state=types.SimpleNamespace())
    deps = types.SimpleNamespace()
    client = object()
    with patch("holon_common.service_runtime.PermissionClient", return_value=client) as ctor:
        got = wire_authz(
            app,
            spicedb_url="http://spicedb",
            spicedb_preshared_key="k",
            opa_url="http://opa",
            deps_module=deps,
        )
    ctor.assert_called_once_with("http://spicedb", "k", "http://opa")
    assert got is client
    assert app.state.authz is client
    assert deps.authz is client


def test_list_audit_events_http_defaults_page_size() -> None:
    pool = object()

    async def _run() -> dict:
        with patch(
            "holon_common.service_runtime.list_events_page",
            new_callable=AsyncMock,
            return_value={"data": [], "nextPageToken": None, "pageSize": 50},
        ) as listed:
            body = await list_audit_events_http(pool, "acme", actor="hl:acme:global:user:jdoe")
        listed.assert_awaited_once_with(
            pool,
            "acme",
            category=None,
            action=None,
            actor_urn="hl:acme:global:user:jdoe",
            outcome=None,
            trace_id=None,
            page_size=50,
            page_token=None,
        )
        return body

    assert asyncio.run(_run())["pageSize"] == 50
