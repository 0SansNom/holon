"""Foundry-style structural Action rules: create/delete object + link.

Property-set edits stay in `declarative._write_instance_edits`. Structural
ops are applied in the same DB transaction and recorded under the reserved
invocation key `__structural__` so compensate/revert can undo them without
breaking the flat `{property: value}` shape used by writeback + response
splatting.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Optional

import asyncpg

from holon_common import Principal

STRUCTURAL_KEY = "__structural__"

_ALLOWED_EDIT_KINDS = frozenset({
    "modify_property",
    "create_link",
    "delete_link",
    "create_object",
    "delete_object",
})


def edit_kind(edit: dict) -> str:
    return edit.get("kind") or "modify_property"


def is_property_edit(edit: dict) -> bool:
    return edit_kind(edit) == "modify_property"


def validate_edit_declaration(edit: dict, *, parameter_names: set[str]) -> None:
    """Structural checks at Action Type registration time."""
    kind = edit_kind(edit)
    if kind not in _ALLOWED_EDIT_KINDS:
        raise ValueError(
            f"malformed edit: unknown kind {kind!r} (expected one of {sorted(_ALLOWED_EDIT_KINDS)})"
        )

    if kind == "modify_property":
        if "property" not in edit or "source" not in edit:
            raise ValueError(f"malformed edit: {edit!r} (expected 'property' and 'source')")
        if edit["source"] not in ("parameter", "literal"):
            raise ValueError(
                f"edit for {edit['property']!r} has unknown source {edit['source']!r} "
                f"(expected 'parameter' or 'literal')"
            )
        if edit["source"] == "parameter":
            if edit.get("parameter_name") not in parameter_names:
                raise ValueError(
                    f"edit for {edit['property']!r} references undeclared parameter "
                    f"{edit.get('parameter_name')!r}"
                )
        elif "value" not in edit:
            raise ValueError(f"edit for {edit['property']!r}: source='literal' requires a 'value'")
        return

    if kind in ("create_link", "delete_link"):
        if not edit.get("relation_type"):
            raise ValueError(f"malformed {kind} edit: expected non-empty 'relation_type'")
        # Ends: each is target_instance | parameter | literal
        for end in ("source", "target"):
            mode = edit.get(f"{end}_from") or ("target_instance" if end == "source" else "parameter")
            if mode not in ("target_instance", "parameter", "literal"):
                raise ValueError(f"malformed {kind} edit: {end}_from must be target_instance|parameter|literal")
            if mode == "parameter" and edit.get(f"{end}_parameter") not in parameter_names:
                raise ValueError(
                    f"malformed {kind} edit: {end}_parameter {edit.get(f'{end}_parameter')!r} undeclared"
                )
            if mode == "literal" and f"{end}_value" not in edit:
                raise ValueError(f"malformed {kind} edit: {end}_from=literal requires '{end}_value'")
        return

    if kind == "create_object":
        if not edit.get("object_type"):
            raise ValueError("malformed create_object edit: expected non-empty 'object_type'")
        pk = edit.get("primary_key") or {}
        pk_source = pk.get("source", "parameter")
        if pk_source not in ("parameter", "generate_uuid", "literal"):
            raise ValueError("create_object primary_key.source must be parameter|generate_uuid|literal")
        if pk_source == "parameter" and pk.get("parameter_name") not in parameter_names:
            raise ValueError(
                f"create_object primary_key references undeclared parameter {pk.get('parameter_name')!r}"
            )
        if pk_source == "literal" and "value" not in pk:
            raise ValueError("create_object primary_key.source=literal requires 'value'")
        for prop in edit.get("properties") or []:
            if "property" not in prop or "source" not in prop:
                raise ValueError(f"malformed create_object property mapping: {prop!r}")
            if prop["source"] == "parameter" and prop.get("parameter_name") not in parameter_names:
                raise ValueError(
                    f"create_object property {prop['property']!r} references undeclared parameter "
                    f"{prop.get('parameter_name')!r}"
                )
            if prop["source"] == "literal" and "value" not in prop:
                raise ValueError(f"create_object property {prop['property']!r}: literal requires 'value'")
        return

    if kind == "delete_object":
        mode = edit.get("target_from") or "target_instance"
        if mode not in ("target_instance", "parameter"):
            raise ValueError("delete_object target_from must be target_instance|parameter")
        if mode == "parameter" and edit.get("parameter_name") not in parameter_names:
            raise ValueError(
                f"delete_object references undeclared parameter {edit.get('parameter_name')!r}"
            )
        return


def _resolve_end(
    edit: dict,
    end: str,
    *,
    target_object_type: str,
    target_instance_id: str,
    parameters: dict[str, Any],
) -> tuple[str, str]:
    """Return (object_type_hint_unused, instance_id) for a link end.

    object_type is resolved from the RelationType at apply time; here we
    only resolve the instance id.
    """
    mode = edit.get(f"{end}_from") or ("target_instance" if end == "source" else "parameter")
    if mode == "target_instance":
        return target_object_type, str(target_instance_id)
    if mode == "parameter":
        value = parameters.get(edit[f"{end}_parameter"])
        if value is None:
            raise ValueError(f"{edit_kind(edit)} edit: missing parameter {edit[f'{end}_parameter']!r}")
        return "", str(value)
    return "", str(edit[f"{end}_value"])


async def _load_relation(conn: asyncpg.Connection, tenant_id: str, relation_name: str) -> dict:
    row = await conn.fetchrow(
        "SELECT * FROM relation_type WHERE tenant_id = $1 AND name = $2",
        tenant_id,
        relation_name,
    )
    if row is None:
        raise ValueError(f"unknown RelationType {relation_name!r}")
    return dict(row)


async def apply_structural_edits(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    workspace_id: str,
    target_object_type: str,
    target_instance_id: str,
    edits: list[dict],
    parameters: dict[str, Any],
    action_urn: str,
    actor: Principal,
    at: datetime,
) -> dict[str, Any]:
    """Apply create/delete link+object rules. Returns the `__structural__` payload."""
    from . import link_overlays

    links: list[dict] = []
    objects: list[dict] = []

    for edit in edits:
        kind = edit_kind(edit)
        if kind == "modify_property":
            continue

        if kind in ("create_link", "delete_link"):
            relation = await _load_relation(conn, tenant_id, edit["relation_type"])
            storage = relation.get("storage_kind") or "foreign_key"
            source_ot = str(relation["source_object_type_urn"]).rsplit(":", 1)[-1]
            target_ot = str(relation["target_object_type_urn"]).rsplit(":", 1)[-1]
            _, source_id = _resolve_end(
                edit, "source",
                target_object_type=target_object_type,
                target_instance_id=target_instance_id,
                parameters=parameters,
            )
            _, neighbor_id = _resolve_end(
                edit, "target",
                target_object_type=target_object_type,
                target_instance_id=target_instance_id,
                parameters=parameters,
            )
            unlink = kind == "delete_link"
            prior: dict[str, Any] = {"storage": storage}

            if storage == "foreign_key":
                fk_property = relation["source_property"]
                # FK always written on the source OT instance.
                existing = await conn.fetchrow(
                    "SELECT property_value FROM object_instance_edit WHERE tenant_id = $1 AND object_type = $2 "
                    "AND instance_id = $3 AND property_name = $4 FOR UPDATE",
                    tenant_id, source_ot, str(source_id), fk_property,
                )
                prior["fk_property"] = fk_property
                prior["source_object_type"] = source_ot
                prior["source_id"] = str(source_id)
                prior["existed"] = existing is not None
                prior["value"] = json.loads(existing["property_value"]) if existing is not None else None
                new_value = None if unlink else neighbor_id
                await conn.execute(
                    """
                    INSERT INTO object_instance_edit
                        (tenant_id, object_type, instance_id, property_name, property_value, set_by_action_urn, set_by_urn, set_at)
                    VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8)
                    ON CONFLICT (tenant_id, object_type, instance_id, property_name) DO UPDATE SET
                        property_value = EXCLUDED.property_value,
                        set_by_action_urn = EXCLUDED.set_by_action_urn,
                        set_by_urn = EXCLUDED.set_by_urn,
                        set_at = EXCLUDED.set_at
                    """,
                    tenant_id, source_ot, str(source_id), fk_property, json.dumps(new_value),
                    action_urn, actor.urn, at,
                )
            elif storage in ("join_dataset", "object_backed"):
                mid_id = f"overlay:{source_id}:{neighbor_id}" if storage == "object_backed" else None
                existing = await conn.fetchrow(
                    "SELECT op, mid_id FROM relation_link_overlay WHERE tenant_id = $1 AND relation_urn = $2 "
                    "AND source_id = $3 AND target_id = $4 FOR UPDATE",
                    tenant_id, relation["urn"], str(source_id), str(neighbor_id),
                )
                prior["relation_urn"] = relation["urn"]
                prior["existed"] = existing is not None
                prior["op"] = existing["op"] if existing else None
                prior["mid_id"] = existing["mid_id"] if existing else None
                await link_overlays.upsert_link(
                    conn,
                    tenant_id=tenant_id,
                    relation_urn=relation["urn"],
                    source_id=str(source_id),
                    target_id=str(neighbor_id),
                    op="delete" if unlink else "add",
                    set_by_urn=actor.urn,
                    set_at=at,
                    mid_id=mid_id,
                )
            else:
                raise ValueError(f"link rules do not support storage {storage!r}")

            links.append({
                "kind": kind,
                "relation_type": edit["relation_type"],
                "source_id": str(source_id),
                "target_id": str(neighbor_id),
                "source_object_type": source_ot,
                "target_object_type": target_ot,
                "prior": prior,
            })
            continue

        if kind == "create_object":
            object_type = edit["object_type"]
            pk = edit.get("primary_key") or {}
            pk_source = pk.get("source", "parameter")
            if pk_source == "generate_uuid":
                new_id = str(uuid.uuid4())
            elif pk_source == "literal":
                new_id = str(pk["value"])
            else:
                raw = parameters.get(pk["parameter_name"])
                if raw is None:
                    raise ValueError(f"create_object missing primary_key parameter {pk.get('parameter_name')!r}")
                new_id = str(raw)

            data: dict[str, Any] = {"id": new_id}
            for prop in edit.get("properties") or []:
                if prop["source"] == "parameter":
                    data[prop["property"]] = parameters.get(prop["parameter_name"])
                else:
                    data[prop["property"]] = prop.get("value")

            # Clear any prior tombstone so recreate works.
            await conn.execute(
                "DELETE FROM object_instance_tombstone WHERE tenant_id = $1 AND object_type = $2 AND instance_id = $3",
                tenant_id, object_type, new_id,
            )
            await conn.execute(
                """
                INSERT INTO object_instance (object_type, tenant_id, instance_id, data, source_snapshot_id, materialized_at)
                VALUES ($1, $2, $3, $4::jsonb, -1, now())
                ON CONFLICT (object_type, tenant_id, instance_id) DO UPDATE SET
                    data = EXCLUDED.data,
                    source_snapshot_id = EXCLUDED.source_snapshot_id,
                    materialized_at = EXCLUDED.materialized_at
                """,
                object_type, tenant_id, new_id, json.dumps(data, default=str),
            )
            objects.append({
                "kind": "create_object",
                "object_type": object_type,
                "instance_id": new_id,
                "data": data,
            })
            continue

        if kind == "delete_object":
            mode = edit.get("target_from") or "target_instance"
            if mode == "target_instance":
                del_type, del_id = target_object_type, str(target_instance_id)
            else:
                del_type = edit.get("object_type") or target_object_type
                raw = parameters.get(edit["parameter_name"])
                if raw is None:
                    raise ValueError(f"delete_object missing parameter {edit.get('parameter_name')!r}")
                del_id = str(raw)

            row = await conn.fetchrow(
                "SELECT data FROM object_instance WHERE object_type = $1 AND tenant_id = $2 AND instance_id = $3",
                del_type, tenant_id, del_id,
            )
            prior_data = json.loads(row["data"]) if row else None
            await conn.execute(
                """
                INSERT INTO object_instance_tombstone
                    (tenant_id, object_type, instance_id, prior_data, set_by_action_urn, set_by_urn, set_at)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
                ON CONFLICT (tenant_id, object_type, instance_id) DO UPDATE SET
                    prior_data = COALESCE(EXCLUDED.prior_data, object_instance_tombstone.prior_data),
                    set_by_action_urn = EXCLUDED.set_by_action_urn,
                    set_by_urn = EXCLUDED.set_by_urn,
                    set_at = EXCLUDED.set_at
                """,
                tenant_id, del_type, del_id,
                json.dumps(prior_data) if prior_data is not None else None,
                action_urn, actor.urn, at,
            )
            objects.append({
                "kind": "delete_object",
                "object_type": del_type,
                "instance_id": del_id,
                "prior_data": prior_data,
            })

    return {"links": links, "objects": objects}


async def revert_structural(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    structural: dict,
    action_urn: str,
    actor: Principal,
    at: datetime,
) -> None:
    """Undo a previously applied `__structural__` payload (most-recent only)."""
    from . import link_overlays

    for link in reversed(structural.get("links") or []):
        prior = link.get("prior") or {}
        storage = prior.get("storage") or "foreign_key"
        if storage == "foreign_key":
            fk_property = prior["fk_property"]
            source_ot = prior["source_object_type"]
            source_id = prior["source_id"]
            if prior.get("existed"):
                await conn.execute(
                    """
                    INSERT INTO object_instance_edit
                        (tenant_id, object_type, instance_id, property_name, property_value, set_by_action_urn, set_by_urn, set_at)
                    VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8)
                    ON CONFLICT (tenant_id, object_type, instance_id, property_name) DO UPDATE SET
                        property_value = EXCLUDED.property_value,
                        set_by_action_urn = EXCLUDED.set_by_action_urn,
                        set_by_urn = EXCLUDED.set_by_urn,
                        set_at = EXCLUDED.set_at
                    """,
                    tenant_id, source_ot, source_id, fk_property, json.dumps(prior.get("value")),
                    action_urn, actor.urn, at,
                )
            else:
                await conn.execute(
                    "DELETE FROM object_instance_edit WHERE tenant_id = $1 AND object_type = $2 "
                    "AND instance_id = $3 AND property_name = $4",
                    tenant_id, source_ot, source_id, fk_property,
                )
        else:
            relation_urn = prior["relation_urn"]
            if prior.get("existed") and prior.get("op"):
                await link_overlays.upsert_link(
                    conn,
                    tenant_id=tenant_id,
                    relation_urn=relation_urn,
                    source_id=link["source_id"],
                    target_id=link["target_id"],
                    op=prior["op"],
                    set_by_urn=actor.urn,
                    set_at=at,
                    mid_id=prior.get("mid_id"),
                )
            else:
                await conn.execute(
                    "DELETE FROM relation_link_overlay WHERE tenant_id = $1 AND relation_urn = $2 "
                    "AND source_id = $3 AND target_id = $4",
                    tenant_id, relation_urn, link["source_id"], link["target_id"],
                )

    for obj in reversed(structural.get("objects") or []):
        if obj["kind"] == "create_object":
            await conn.execute(
                "DELETE FROM object_instance WHERE object_type = $1 AND tenant_id = $2 AND instance_id = $3 "
                "AND source_snapshot_id = -1",
                obj["object_type"], tenant_id, obj["instance_id"],
            )
            await conn.execute(
                "DELETE FROM object_instance_edit WHERE tenant_id = $1 AND object_type = $2 AND instance_id = $3",
                tenant_id, obj["object_type"], obj["instance_id"],
            )
        elif obj["kind"] == "delete_object":
            await conn.execute(
                "DELETE FROM object_instance_tombstone WHERE tenant_id = $1 AND object_type = $2 AND instance_id = $3",
                tenant_id, obj["object_type"], obj["instance_id"],
            )


def property_edit_keys(edits: Optional[dict]) -> list[str]:
    """Keys that are real property overlays (skip reserved structural bag)."""
    if not edits:
        return []
    return [k for k in edits.keys() if k != STRUCTURAL_KEY]


def split_result_for_response(result: dict) -> dict:
    """Drop internal keys before splatting into the HTTP response."""
    return {k: v for k, v in result.items() if k != STRUCTURAL_KEY}
