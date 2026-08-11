"""Object Sets — filtered collections of object instances (Foundry Object Set).

Knowledge-owned ontology artefact (not Experience Collections). Evaluation
always goes through `_resolve_many` + PDP so markings/classification apply.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import asyncpg

from .object_types import get_object_type
from .urns import object_type_urn

VALID_OPS = frozenset({"eq", "neq", "in", "gt", "gte", "lt", "lte", "contains"})
VALID_LIFECYCLE = frozenset({"experimental", "active", "deprecated"})
VALID_VISIBILITY = frozenset({"prominent", "normal", "hidden"})


def object_set_urn(tenant_id: str, workspace_id: str, name: str) -> str:
    from holon_common import build_urn

    return build_urn(tenant_id, workspace_id, "object-set", name)


def _parse_row(row: asyncpg.Record) -> dict:
    result = dict(row)
    if isinstance(result.get("definition"), str):
        result["definition"] = json.loads(result["definition"])
    return result


def validate_definition(definition: dict, property_mapping: dict) -> None:
    if not isinstance(definition, dict):
        raise ValueError("definition must be an object")
    predicates = definition.get("all")
    if predicates is None:
        raise ValueError("definition.all is required (list of predicates)")
    if not isinstance(predicates, list):
        raise ValueError("definition.all must be a list")
    for pred in predicates:
        if not isinstance(pred, dict):
            raise ValueError("each predicate must be an object")
        prop = pred.get("property")
        op = pred.get("op")
        if not prop or prop not in property_mapping:
            raise ValueError(f"predicate property {prop!r} must be a key in the ObjectType property_mapping")
        if op not in VALID_OPS:
            raise ValueError(f"invalid predicate op {op!r} (must be one of {sorted(VALID_OPS)})")
        if "value" not in pred:
            raise ValueError("predicate.value is required")
        if op == "in" and not isinstance(pred["value"], list):
            raise ValueError("op 'in' requires value to be a list")


def matches_predicates(instance: dict, definition: dict, property_mapping: dict) -> bool:
    """Evaluate definition.all against a resolved instance (api-name keys preferred)."""
    for pred in definition.get("all") or []:
        prop = pred["property"]
        op = pred["op"]
        expected = pred["value"]
        actual = instance.get(prop)
        if actual is None:
            col = property_mapping.get(prop)
            if col:
                actual = instance.get(col)
        if op == "eq" and not (actual == expected):
            return False
        if op == "neq" and not (actual != expected):
            return False
        if op == "in" and actual not in expected:
            return False
        if op == "gt" and not (actual is not None and actual > expected):
            return False
        if op == "gte" and not (actual is not None and actual >= expected):
            return False
        if op == "lt" and not (actual is not None and actual < expected):
            return False
        if op == "lte" and not (actual is not None and actual <= expected):
            return False
        if op == "contains":
            if actual is None or expected not in str(actual):
                return False
    return True


async def ensure_schema(conn: asyncpg.Connection) -> None:
    # Table created via object_types.DDL; no-op helper for symmetry.
    return None


async def create_object_set(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    object_type: str,
    definition: dict,
    display_name: str = "",
    description: str = "",
    lifecycle_status: str = "experimental",
    visibility: str = "normal",
) -> dict:
    if lifecycle_status not in VALID_LIFECYCLE:
        raise ValueError(f"invalid lifecycle_status: {lifecycle_status!r}")
    if visibility not in VALID_VISIBILITY:
        raise ValueError(f"invalid visibility: {visibility!r}")
    ot_urn = object_type_urn(tenant_id, workspace_id, object_type)
    ot = await get_object_type(pool, ot_urn)
    if ot is None:
        raise ValueError(f"unknown ObjectType: {object_type}")
    validate_definition(definition, ot["property_mapping"])
    urn = object_set_urn(tenant_id, workspace_id, name)
    try:
        await pool.execute(
            """
            INSERT INTO object_set (
                urn, tenant_id, workspace_id, name, display_name, description,
                object_type_urn, definition, lifecycle_status, visibility
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
            """,
            urn,
            tenant_id,
            workspace_id,
            name,
            display_name or name,
            description,
            ot_urn,
            json.dumps(definition),
            lifecycle_status,
            visibility,
        )
    except asyncpg.UniqueViolationError as exc:
        raise ValueError(f"object set already exists: {name}") from exc
    return await get_object_set(pool, urn)  # type: ignore[return-value]


async def update_object_set(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    definition: Optional[dict] = None,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    lifecycle_status: Optional[str] = None,
    visibility: Optional[str] = None,
) -> dict:
    urn = object_set_urn(tenant_id, workspace_id, name)
    current = await get_object_set(pool, urn)
    if current is None:
        raise ValueError(f"unknown object set: {name}")
    ot = await get_object_type(pool, current["object_type_urn"])
    if ot is None:
        raise ValueError("backing ObjectType no longer exists")
    new_def = definition if definition is not None else current["definition"]
    validate_definition(new_def, ot["property_mapping"])
    new_lifecycle = lifecycle_status if lifecycle_status is not None else current["lifecycle_status"]
    new_visibility = visibility if visibility is not None else current["visibility"]
    if new_lifecycle not in VALID_LIFECYCLE:
        raise ValueError(f"invalid lifecycle_status: {new_lifecycle!r}")
    if new_visibility not in VALID_VISIBILITY:
        raise ValueError(f"invalid visibility: {new_visibility!r}")
    await pool.execute(
        """
        UPDATE object_set SET
            definition = $1::jsonb,
            display_name = $2,
            description = $3,
            lifecycle_status = $4,
            visibility = $5
        WHERE urn = $6
        """,
        json.dumps(new_def),
        display_name if display_name is not None else current["display_name"],
        description if description is not None else current["description"],
        new_lifecycle,
        new_visibility,
        urn,
    )
    return await get_object_set(pool, urn)  # type: ignore[return-value]


async def get_object_set(pool: asyncpg.Pool, urn: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM object_set WHERE urn = $1", urn)
    return _parse_row(row) if row else None


async def list_object_sets(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM object_set WHERE tenant_id = $1 ORDER BY name", tenant_id
    )
    return [_parse_row(r) for r in rows]
