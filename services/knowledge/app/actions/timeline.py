"""Instance timeline read model."""

from __future__ import annotations

import asyncpg


async def list_instance_timeline(pool: asyncpg.Pool, tenant_id: str, instance_urn: str, limit: int = 100) -> list[dict]:
    invocations = await pool.fetch(
        """
        SELECT id, action_name, actor_urn, reason, invoked_at AS at, edits IS NOT NULL AS has_edits, reverted_at
        FROM action_invocation
        WHERE tenant_id = $1 AND instance_urn = $2
        """,
        tenant_id, instance_urn,
    )

    from .. import ontology

    writeback_action_names: set[str] = set()
    for action_name in {row["action_name"] for row in invocations if row["has_edits"]}:
        action_type = await ontology.get_action_type(pool, tenant_id, action_name)
        if action_type and action_type.get("writeback_dataset"):
            writeback_action_names.add(action_name)
    approvals = await pool.fetch(
        """
        SELECT action_name, requested_by_urn, reason, status, requested_at, decided_by_urn, decided_at, expires_at
        FROM action_approval
        WHERE tenant_id = $1 AND instance_urn = $2
        """,
        tenant_id, instance_urn,
    )

    events: list[dict] = []
    for row in invocations:
        events.append({
            "kind": "invoked",
            "action_name": row["action_name"],
            "actor_urn": row["actor_urn"],
            "reason": row["reason"],
            "at": row["at"],
            "id": row["id"],
            "has_edits": row["has_edits"],
            "revertible": row["has_edits"] and row["action_name"] not in writeback_action_names,
            "reverted": row["reverted_at"] is not None,
        })
    for row in approvals:
        events.append({
            "kind": "requested",
            "action_name": row["action_name"],
            "actor_urn": row["requested_by_urn"],
            "reason": row["reason"],
            "at": row["requested_at"],
            "id": None,
            "has_edits": False,
            "revertible": False,
            "reverted": False,
        })
        if row["status"] == "rejected":
            events.append({
                "kind": "rejected",
                "action_name": row["action_name"],
                "actor_urn": row["decided_by_urn"],
                "reason": row["reason"],
                "at": row["decided_at"],
                "id": None,
                "has_edits": False,
                "revertible": False,
                "reverted": False,
            })
        elif row["status"] == "expired":
            events.append({
                "kind": "expired",
                "action_name": row["action_name"],
                "actor_urn": None,
                "reason": row["reason"],
                "at": row["expires_at"],
                "id": None,
                "has_edits": False,
                "revertible": False,
                "reverted": False,
            })

    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]
