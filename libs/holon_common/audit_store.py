"""Durable audit_event store — append-only, tenant-scoped, queryable."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any, Optional

import asyncpg

from .audit import CATEGORIES, register_durable_audit
from .errors import HolonError

DDL = """
CREATE TABLE IF NOT EXISTS audit_event (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    category TEXT NOT NULL,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL,
    actor_urn TEXT,
    actor_type TEXT,
    resource_type TEXT,
    resource_urn TEXT,
    permission TEXT,
    reason TEXT,
    trace_id TEXT,
    request_id TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS audit_event_tenant_occurred_idx
    ON audit_event (tenant_id, occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS audit_event_tenant_category_idx
    ON audit_event (tenant_id, category, occurred_at DESC);
CREATE INDEX IF NOT EXISTS audit_event_tenant_actor_idx
    ON audit_event (tenant_id, actor_urn, occurred_at DESC);
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


async def insert_from_record(pool: asyncpg.Pool, record: dict[str, Any]) -> int:
    """Persist a record produced by ``emit_audit`` / ``build_audit_record``."""
    occurred_at = record.get("occurredAt")
    if isinstance(occurred_at, str):
        occurred = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    else:
        occurred = None
    details = record.get("extra") or {}
    return await pool.fetchval(
        """
        INSERT INTO audit_event (
            tenant_id, occurred_at, category, action, outcome,
            actor_urn, actor_type, resource_type, resource_urn,
            permission, reason, trace_id, request_id, details
        ) VALUES (
            $1, COALESCE($2, now()), $3, $4, $5,
            $6, $7, $8, $9,
            $10, $11, $12, $13, $14::jsonb
        ) RETURNING id
        """,
        record.get("tenantId") or "unknown",
        occurred,
        record["category"],
        record["action"],
        record["outcome"],
        record.get("actor"),
        record.get("actorType"),
        record.get("resourceType"),
        record.get("resourceUrn"),
        record.get("permission"),
        record.get("reason"),
        record.get("traceId"),
        record.get("requestId"),
        json.dumps(details),
    )


def install_durable_audit(pool: asyncpg.Pool) -> None:
    """Register a durable sink that writes into ``pool``'s audit_event table."""

    async def _hook(record: dict[str, Any]) -> None:
        await insert_from_record(pool, record)

    register_durable_audit(_hook)


async def list_events(
    pool: asyncpg.Pool,
    tenant_id: str,
    *,
    category: Optional[str] = None,
    action: Optional[str] = None,
    actor_urn: Optional[str] = None,
    outcome: Optional[str] = None,
    occurred_after: Optional[datetime] = None,
    occurred_before: Optional[datetime] = None,
    after_id: Optional[int] = None,
    page_size: int = 50,
) -> list[dict]:
    clauses = ["tenant_id = $1"]
    args: list[Any] = [tenant_id]
    idx = 2
    if category:
        clauses.append(f"category = ${idx}")
        args.append(category)
        idx += 1
    if action:
        clauses.append(f"action = ${idx}")
        args.append(action)
        idx += 1
    if actor_urn:
        clauses.append(f"actor_urn = ${idx}")
        args.append(actor_urn)
        idx += 1
    if outcome:
        clauses.append(f"outcome = ${idx}")
        args.append(outcome)
        idx += 1
    if occurred_after is not None:
        clauses.append(f"occurred_at >= ${idx}")
        args.append(occurred_after)
        idx += 1
    if occurred_before is not None:
        clauses.append(f"occurred_at <= ${idx}")
        args.append(occurred_before)
        idx += 1
    if after_id is not None:
        clauses.append(f"id < ${idx}")
        args.append(after_id)
        idx += 1
    args.append(page_size)
    where = " AND ".join(clauses)
    rows = await pool.fetch(
        f"""
        SELECT id, tenant_id, occurred_at, category, action, outcome,
               actor_urn, actor_type, resource_type, resource_urn,
               permission, reason, trace_id, request_id, details
        FROM audit_event
        WHERE {where}
        ORDER BY id DESC
        LIMIT ${idx}
        """,
        *args,
    )
    return [_row_to_wire(r) for r in rows]


def _row_to_wire(row: asyncpg.Record) -> dict:
    details = row["details"]
    if isinstance(details, str):
        details = json.loads(details)
    return {
        "id": row["id"],
        "tenantId": row["tenant_id"],
        "occurredAt": row["occurred_at"].isoformat() if row["occurred_at"] else None,
        "category": row["category"],
        "action": row["action"],
        "outcome": row["outcome"],
        "actor": row["actor_urn"],
        "actorType": row["actor_type"],
        "resourceType": row["resource_type"],
        "resourceUrn": row["resource_urn"],
        "permission": row["permission"],
        "reason": row["reason"],
        "traceId": row["trace_id"],
        "requestId": row["request_id"],
        "extra": details or {},
    }


def decode_page_token(page_token: Optional[str]) -> Optional[int]:
    if not page_token:
        return None
    try:
        padded = page_token + "=" * (-len(page_token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
        return int(payload["after_id"])
    except Exception as exc:
        raise HolonError.invalid_argument("InvalidPageToken", "invalid pageToken") from exc


def encode_page_token(*, after_id: int) -> str:
    raw = json.dumps({"after_id": after_id}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


async def list_events_page(
    pool: asyncpg.Pool,
    tenant_id: str,
    *,
    category: Optional[str] = None,
    action: Optional[str] = None,
    actor_urn: Optional[str] = None,
    outcome: Optional[str] = None,
    page_size: int = 50,
    page_token: Optional[str] = None,
) -> dict[str, Any]:
    """Wire-shaped page used by every service ``GET …/audit-events``."""
    if category is not None and category not in CATEGORIES:
        raise HolonError.invalid_argument(
            "InvalidAuditCategory", f"unknown category: {category}", category=category
        )
    if page_size < 1 or page_size > 100:
        raise HolonError.invalid_argument("InvalidPageSize", "pageSize must be between 1 and 100")
    after_id = decode_page_token(page_token)
    rows = await list_events(
        pool,
        tenant_id,
        category=category,
        action=action,
        actor_urn=actor_urn,
        outcome=outcome,
        after_id=after_id,
        page_size=page_size + 1,
    )
    next_token = None
    if len(rows) > page_size:
        rows = rows[:page_size]
        next_token = encode_page_token(after_id=rows[-1]["id"])
    return {"data": rows, "nextPageToken": next_token, "pageSize": page_size}
