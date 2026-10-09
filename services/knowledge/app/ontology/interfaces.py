"""Interface registry — polymorphic contracts across ObjectTypes."""
from __future__ import annotations

import json
from typing import Optional

import asyncpg

from .lifecycle import normalize_deprecation_metadata
from .interface_contract import (  # noqa: F401
    _INTERFACE_PROPERTY_TYPE_KINDS,
    _LINK_CARDINALITIES,
    _LINK_TARGET_KINDS,
    _merge_link_constraints,
    _merge_property_types,
    ancestor_interface_names,
    descendant_interface_names,
    effective_interface_contract,
    expand_implements,
    link_constraint_identity,
    link_constraints_tighten,
    object_type_names_for_interface,
    outbound_cardinality_from_relation,
    property_type_binding_key,
    property_types_tighten,
    relation_other_endpoint,
    resolve_interface_property_path,
    validate_interface_property_types,
    validate_link_constraints,
    validate_parent_interfaces,
)


async def create_interface_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    required_properties: list[str],
    required_actions: list[str],
    description: str = "",
    lifecycle_status: str = "experimental",
    deprecation_reason: Optional[str] = None,
    deprecation_deadline=None,
    replacement_urn: Optional[str] = None,
    property_types: Optional[dict] = None,
    link_constraints: Optional[list] = None,
    parent_interfaces: Optional[list] = None,
) -> dict:
    dep = normalize_deprecation_metadata(
        lifecycle_status,
        deprecation_reason=deprecation_reason,
        deprecation_deadline=deprecation_deadline,
        replacement_urn=replacement_urn,
    )
    normalized_parents = await validate_parent_interfaces(
        pool, tenant_id=tenant_id, interface_name=name, parent_interfaces=parent_interfaces or [],
    )
    allowed_properties = set(required_properties)
    for parent in normalized_parents:
        parent_eff = await effective_interface_contract(pool, tenant_id, parent)
        allowed_properties.update(parent_eff["required_properties"])
    normalized_types = await validate_interface_property_types(
        pool,
        tenant_id=tenant_id,
        required_properties=sorted(allowed_properties),
        property_types=property_types or {},
    )
    # Drop types that aren't on this interface's own required list and aren't
    # refining a parent-required property — already constrained by allowed set.
    normalized_links = await validate_link_constraints(
        pool, tenant_id=tenant_id, link_constraints=link_constraints or [],
    )
    # Surface parent merge conflicts before insert.
    await effective_interface_contract(
        pool,
        tenant_id,
        name,
        override={
            "parent_interfaces": normalized_parents,
            "required_properties": required_properties,
            "required_actions": required_actions,
            "property_types": normalized_types,
            "link_constraints": normalized_links,
        },
    )
    await pool.execute(
        """
        INSERT INTO interface_type (
            tenant_id, name, required_properties, required_actions, description,
            lifecycle_status, deprecation_reason, deprecation_deadline, replacement_urn,
            property_types, link_constraints, parent_interfaces
        )
        VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6, $7, $8, $9, $10::jsonb, $11::jsonb, $12::jsonb)
        """,
        tenant_id,
        name,
        json.dumps(required_properties),
        json.dumps(required_actions),
        description,
        dep["lifecycle_status"],
        dep["deprecation_reason"],
        dep["deprecation_deadline"],
        dep["replacement_urn"],
        json.dumps(normalized_types),
        json.dumps(normalized_links),
        json.dumps(normalized_parents),
    )
    return await get_interface_type(pool, tenant_id, name)

async def update_interface_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    required_properties: Optional[list[str]] = None,
    required_actions: Optional[list[str]] = None,
    description: Optional[str] = None,
    lifecycle_status: Optional[str] = None,
    deprecation_reason: Optional[str] = None,
    deprecation_deadline=None,
    replacement_urn: Optional[str] = None,
    property_types: Optional[dict] = None,
    link_constraints: Optional[list] = None,
    parent_interfaces: Optional[list] = None,
) -> dict:
    """Partial update — `name` is deliberately not an accepted param: it's
    the key referenced from every ObjectType's `implements` list.
    `None` means "leave unchanged".
    """
    current = await get_interface_type(pool, tenant_id, name)
    if current is None:
        raise ValueError(f"unknown interface: {name!r}")

    new_required_properties = current["required_properties"] if required_properties is None else required_properties
    new_required_actions = current["required_actions"] if required_actions is None else required_actions
    new_description = current["description"] if description is None else description
    new_lifecycle = current.get("lifecycle_status") or "experimental"
    if lifecycle_status is not None:
        new_lifecycle = lifecycle_status
    new_dep_reason = (
        current.get("deprecation_reason") if deprecation_reason is None else deprecation_reason
    )
    new_dep_deadline = (
        current.get("deprecation_deadline") if deprecation_deadline is None else deprecation_deadline
    )
    new_replacement = current.get("replacement_urn") if replacement_urn is None else replacement_urn
    dep = normalize_deprecation_metadata(
        new_lifecycle,
        deprecation_reason=new_dep_reason,
        deprecation_deadline=new_dep_deadline,
        replacement_urn=new_replacement,
    )

    if parent_interfaces is None:
        new_parents = current.get("parent_interfaces") or []
    else:
        new_parents = await validate_parent_interfaces(
            pool, tenant_id=tenant_id, interface_name=name, parent_interfaces=parent_interfaces,
        )

    allowed_properties = set(new_required_properties)
    for parent in new_parents:
        parent_eff = await effective_interface_contract(pool, tenant_id, parent)
        allowed_properties.update(parent_eff["required_properties"])

    if property_types is None:
        kept = {
            key: rule
            for key, rule in (current.get("property_types") or {}).items()
            if key in allowed_properties
        }
        new_property_types = kept
    else:
        new_property_types = await validate_interface_property_types(
            pool,
            tenant_id=tenant_id,
            required_properties=sorted(allowed_properties),
            property_types=property_types,
        )

    if link_constraints is None:
        new_link_constraints = current.get("link_constraints") or []
    else:
        new_link_constraints = await validate_link_constraints(
            pool, tenant_id=tenant_id, link_constraints=link_constraints,
        )

    proposed_override = {
        "parent_interfaces": new_parents,
        "required_properties": new_required_properties,
        "required_actions": new_required_actions,
        "property_types": new_property_types,
        "link_constraints": new_link_constraints,
    }
    # Merge conflicts (parent diamond / override clash).
    await effective_interface_contract(pool, tenant_id, name, override=proposed_override)

    if (
        required_properties is not None
        or required_actions is not None
        or property_types is not None
        or link_constraints is not None
        or parent_interfaces is not None
    ):
        from .publishing import assert_interface_tighten_compatible

        await assert_interface_tighten_compatible(
            pool,
            tenant_id=tenant_id,
            interface_name=name,
            previous_properties=current["required_properties"],
            previous_actions=current["required_actions"],
            new_properties=new_required_properties,
            new_actions=new_required_actions,
            previous_property_types=current.get("property_types") or {},
            new_property_types=new_property_types,
            previous_link_constraints=current.get("link_constraints") or [],
            new_link_constraints=new_link_constraints,
            previous_parent_interfaces=current.get("parent_interfaces") or [],
            new_parent_interfaces=new_parents,
        )

    await pool.execute(
        """
        UPDATE interface_type SET
            required_properties = $1::jsonb,
            required_actions = $2::jsonb,
            description = $3,
            lifecycle_status = $4,
            deprecation_reason = $5,
            deprecation_deadline = $6,
            replacement_urn = $7,
            property_types = $8::jsonb,
            link_constraints = $9::jsonb,
            parent_interfaces = $10::jsonb
        WHERE tenant_id = $11 AND name = $12
        """,
        json.dumps(new_required_properties),
        json.dumps(new_required_actions),
        new_description,
        dep["lifecycle_status"],
        dep["deprecation_reason"],
        dep["deprecation_deadline"],
        dep["replacement_urn"],
        json.dumps(new_property_types),
        json.dumps(new_link_constraints),
        json.dumps(new_parents),
        tenant_id,
        name,
    )
    return await get_interface_type(pool, tenant_id, name)

def _parse_interface_row(row: asyncpg.Record) -> dict:
    result = dict(row)
    for key in (
        "required_properties",
        "required_actions",
        "property_types",
        "link_constraints",
        "parent_interfaces",
    ):
        if key not in result:
            continue
        if isinstance(result[key], str):
            result[key] = json.loads(result[key])
    result.setdefault("lifecycle_status", "experimental")
    result.setdefault("property_types", {})
    if result.get("property_types") is None:
        result["property_types"] = {}
    result.setdefault("link_constraints", [])
    if result.get("link_constraints") is None:
        result["link_constraints"] = []
    result.setdefault("parent_interfaces", [])
    if result.get("parent_interfaces") is None:
        result["parent_interfaces"] = []
    return result

async def get_interface_type(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM interface_type WHERE tenant_id = $1 AND name = $2", tenant_id, name)
    return _parse_interface_row(row) if row else None

async def list_interface_types(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM interface_type WHERE tenant_id = $1 ORDER BY name", tenant_id)
    return [_parse_interface_row(row) for row in rows]

async def delete_interface_type(
    pool: asyncpg.Pool, *, tenant_id: str, name: str
) -> dict:
    """Hard-delete an Interface. Refuses active lifecycle (RelationType
    convention), published implementers (direct or via child extends),
    and child interfaces that still extend this one.
    """
    current = await get_interface_type(pool, tenant_id, name)
    if current is None:
        raise ValueError(f"unknown interface: {name!r}")
    if (current.get("lifecycle_status") or "experimental") == "active":
        raise ValueError(
            "cannot delete an active interface — set lifecycle_status to deprecated "
            "(or experimental) first"
        )
    children = sorted(await descendant_interface_names(pool, tenant_id, name))
    if children:
        raise ValueError(
            f"cannot delete interface {name!r}: extended by {children}"
        )
    implementers = await object_type_names_for_interface(pool, tenant_id, name)
    if implementers:
        raise ValueError(
            f"cannot delete interface {name!r}: "
            f"{len(implementers)} implementer(s) still declare it — {implementers}"
        )
    await pool.execute(
        "DELETE FROM interface_type WHERE tenant_id = $1 AND name = $2",
        tenant_id,
        name,
    )
    return current

