"""Structured audit channel (R11.1) — distinct from application logs.

Emits JSON lines with `audit=true` on logger `holon.audit` so operators
can ship this stdout stream to their SIEM (fluent-bit, Vector, etc.).
We do not run a Kafka producer here — that is the deployer's piping job.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

_audit_logger = logging.getLogger("holon.audit")
if not _audit_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    _audit_logger.addHandler(handler)
    _audit_logger.setLevel(logging.INFO)
    _audit_logger.propagate = False


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
    extra: Optional[dict[str, Any]] = None,
) -> None:
    record = {
        "audit": True,
        "ts": time.time(),
        "action": action,
        "outcome": outcome,
        "tenantId": tenant_id,
        "actor": actor_urn,
        "resourceType": resource_type,
        "resourceUrn": resource_urn,
        "reason": reason,
        "traceId": trace_id,
    }
    if extra:
        record["extra"] = extra
    _audit_logger.info(json.dumps(record, default=str))
