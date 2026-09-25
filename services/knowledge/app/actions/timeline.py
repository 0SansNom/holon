"""Instance timeline read model."""

from __future__ import annotations

import json

import asyncpg

from ..action_structural import STRUCTURAL_KEY, property_edit_keys


def property_changes(edits, prior_values) -> list[dict]:
    """Before/after for an invocation. Skips the structural bag as a property."""
    if isinstance(edits, str):
        edits = json.loads(edits)
    if isinstance(prior_values, str):
        prior_values = json.loads(prior_values)
    if not isinstance(edits, dict):
        return []
    prior = prior_values if isinstance(prior_values, dict) else {}
    changes: list[dict] = []
    for key in property_edit_keys(edits):
        entry = prior.get(key)
        if isinstance(entry, dict) and "existed" in entry:
            before = entry.get("value") if entry.get("existed") else None
        else:
            before = None
        changes.append({"property": key, "before": before, "after": edits.get(key)})
    structural = edits.get(STRUCTURAL_KEY)
    if isinstance(structural, dict):
        for link in structural.get("links") or []:
            changes.append({
                "property": link.get("relation_type") or "link",
                "before": None,
                "after": (
                    f"{link.get('kind')} {link.get('source_object_type')}:{link.get('source_id')}"
                    f" → {link.get('target_object_type')}:{link.get('target_id')}"
                ),
            })
        for obj in structural.get("objects") or []:
            changes.append({
                "property": obj.get("object_type") or "object",
                "before": None,
                "after": f"{obj.get('kind')} {obj.get('instance_id')}",
            })
    return changes


async def list_instance_timeline(pool: asyncpg.Pool, tenant_id: str, instance_urn: str, limit: int = 100) -> list[dict]:
    invocations = await pool.fetch(
        """
        SELECT id, action_name, actor_urn, reason, invoked_at AS at, edits, prior_values, reverted_at
        FROM action_invocation
        WHERE tenant_id = $1 AND instance_urn = $2
        """,
        tenant_id, instance_urn,
    )

    from .. import ontology

    writeback_action_names: set[str] = set()
    for action_name in {row["action_name"] for row in invocations if row["edits"] is not None}:
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
            "has_edits": row["edits"] is not None,
            "changes": property_changes(row["edits"], row["prior_values"]),
            "revertible": row["edits"] is not None and row["action_name"] not in writeback_action_names,
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
            "changes": [],
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
                "changes": [],
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
                "changes": [],
                "revertible": False,
                "reverted": False,
            })

    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]
