"""Structured audit channel — stdout for SIEM + optional durable sinks.

Operators ship ``holon.audit`` JSON lines to a SIEM. Services that own a
Postgres pool may also ``install_durable_audit(pool)`` so the same records
are queryable in-platform immediately (no file-polling lag).

Schema version 1 answers who / what / when / where / outcome, with an
enforced ``category`` for filtering.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

_audit_logger = logging.getLogger("holon.audit")
if not _audit_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    _audit_logger.addHandler(handler)
    _audit_logger.setLevel(logging.INFO)
    _audit_logger.propagate = False

SCHEMA_VERSION = 1

# Categories are closed for predictable SIEM / query filters.
CATEGORIES = frozenset({"authz", "action", "identity", "ontology", "access"})

DurableAuditHook = Callable[[dict[str, Any]], Awaitable[None]]
_durable_hooks: list[DurableAuditHook] = []


def register_durable_audit(hook: DurableAuditHook) -> None:
    """Register an async sink (typically Postgres). Called from service lifespan."""
    if hook not in _durable_hooks:
        _durable_hooks.append(hook)


def clear_durable_audit_hooks() -> None:
    """Test helper — drop all durable sinks."""
    _durable_hooks.clear()


def build_audit_record(
    *,
    category: str,
    action: str,
    outcome: str,
    tenant_id: Optional[str] = None,
    actor_urn: Optional[str] = None,
    actor_type: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_urn: Optional[str] = None,
    permission: Optional[str] = None,
    reason: Optional[str] = None,
    trace_id: Optional[str] = None,
    request_id: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if category not in CATEGORIES:
        raise ValueError(f"unknown audit category: {category!r}")
    now = datetime.now(timezone.utc)
    record: dict[str, Any] = {
        "audit": True,
        "schemaVersion": SCHEMA_VERSION,
        "ts": time.time(),
        "occurredAt": now.isoformat(),
        "category": category,
        "action": action,
        "outcome": outcome,
        "tenantId": tenant_id,
        "actor": actor_urn,
        "actorType": actor_type,
        "resourceType": resource_type,
        "resourceUrn": resource_urn,
        "permission": permission,
        "reason": reason,
        "traceId": trace_id,
        "requestId": request_id,
    }
    if extra:
        record["extra"] = extra
    return record


def emit_audit(
    *,
    action: str,
    outcome: str,
    tenant_id: Optional[str] = None,
    actor_urn: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_urn: Optional[str] = None,
    reason: Optional[str] = None,
    trace_id: Optional[str] = None,
    category: str = "access",
    permission: Optional[str] = None,
    actor_type: Optional[str] = None,
    request_id: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Emit one audit record to stdout and any registered durable sinks."""
    record = build_audit_record(
        category=category,
        action=action,
        outcome=outcome,
        tenant_id=tenant_id,
        actor_urn=actor_urn,
        actor_type=actor_type,
        resource_type=resource_type,
        resource_urn=resource_urn,
        permission=permission,
        reason=reason,
        trace_id=trace_id,
        request_id=request_id,
        extra=extra,
    )
    _audit_logger.info(json.dumps(record, default=str))
    if _durable_hooks:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            for hook in _durable_hooks:
                loop.create_task(_safe_durable(hook, record))
    return record


async def _safe_durable(hook: DurableAuditHook, record: dict[str, Any]) -> None:
    try:
        await hook(record)
    except Exception:
        _audit_logger.exception("durable audit sink failed for action=%s", record.get("action"))
