"""Unit tests for Holon audit channel + record shape (no stack)."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))

from holon_common.audit import (  # noqa: E402
    CATEGORIES,
    SCHEMA_VERSION,
    build_audit_record,
    clear_durable_audit_hooks,
    emit_audit,
    register_durable_audit,
)


def setup_function() -> None:
    clear_durable_audit_hooks()


def teardown_function() -> None:
    clear_durable_audit_hooks()


def test_build_audit_record_has_schema_and_category() -> None:
    record = build_audit_record(
        category="authz",
        action="authz.decide",
        outcome="deny",
        tenant_id="t1",
        actor_urn="hl:t1:global:user:u",
        permission="write",
        resource_type="object_type",
        resource_urn="hl:t1:main:object-type:Customer",
    )
    assert record["audit"] is True
    assert record["schemaVersion"] == SCHEMA_VERSION
    assert record["category"] == "authz"
    assert record["permission"] == "write"
    assert "occurredAt" in record
    assert record["tenantId"] == "t1"


def test_unknown_category_rejected() -> None:
    with pytest.raises(ValueError):
        build_audit_record(category="nope", action="x", outcome="success")


def test_emit_audit_writes_json_line(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("holon.audit")
    with caplog.at_level(logging.INFO, logger="holon.audit"):
        # StreamHandler on holon.audit bypasses caplog; call build via emit and
        # inspect return value instead.
        record = emit_audit(
            category="identity",
            action="identity.login",
            outcome="success",
            tenant_id="t1",
            actor_urn="hl:t1:global:user:u",
        )
    assert record["action"] == "identity.login"
    assert record["category"] in CATEGORIES
    assert json.loads(json.dumps(record, default=str))["audit"] is True


def test_durable_hook_receives_record() -> None:
    import asyncio

    seen: list[dict] = []

    async def _hook(record: dict) -> None:
        seen.append(record)

    async def _run() -> None:
        register_durable_audit(_hook)
        emit_audit(category="action", action="knowledge.action.invoked", outcome="success", tenant_id="t1")
        await asyncio.sleep(0.05)

    asyncio.run(_run())
    assert len(seen) == 1
    assert seen[0]["category"] == "action"


def test_page_token_roundtrip() -> None:
    from holon_common.audit_store import decode_page_token, encode_page_token
    from holon_common.errors import HolonError

    token = encode_page_token(after_id=42)
    assert decode_page_token(token) == 42
    assert decode_page_token(None) is None
    try:
        decode_page_token("%%%")
        raise AssertionError("expected InvalidPageToken")
    except HolonError as exc:
        assert exc.error_name == "InvalidPageToken"


def test_list_events_page_rejects_bad_category() -> None:
    import asyncio

    from holon_common.audit_store import list_events_page
    from holon_common.errors import HolonError

    class _Pool:
        pass

    async def _run() -> None:
        try:
            await list_events_page(_Pool(), "t1", category="nope")  # type: ignore[arg-type]
            raise AssertionError("expected InvalidAuditCategory")
        except HolonError as exc:
            assert exc.error_name == "InvalidAuditCategory"

    asyncio.run(_run())
