"""Interface registry — polymorphic contracts and property/action definitions across ObjectTypes."""

from __future__ import annotations

import json
from typing import Optional

import asyncpg

from .lifecycle import normalize_deprecation_metadata

# Interface-typed properties are leaves only.
# Struct/array belong on concrete ObjectTypes, not the abstract contract.
_INTERFACE_PROPERTY_TYPE_KINDS = frozenset({"value_type", "shared_property_type"})


def property_type_binding_key(rule: object) -> tuple[str, str] | None:
    """Stable identity for a typed leaf — used to compare interface
    contract ↔ ObjectType `property_types` at publish / tighten time.
    """
    if not isinstance(rule, dict):
        return None
    kind = rule.get("kind")
    if kind == "value_type":
        name = rule.get("value_type")
        return ("value_type", name) if isinstance(name, str) and name else None
    if kind == "shared_property_type":
        name = rule.get("shared_property_type")
        return ("shared_property_type", name) if isinstance(name, str) and name else None
    return None


def resolve_interface_property_path(
    *,
    property_mapping: dict,
    property_types: dict,
    interface_prop: str,
    binding_path: object = None,
) -> tuple[str, object | None]:
    """Resolve where an interface required property is satisfied on an OT.

    ``binding_path`` is either absent (same-name top-level property) or a
    one-level struct path like ``address.city`` (Foundry struct-field →
    interface property). Returns ``(top_level_name, leaf_type_rule)``.
    """
    path = binding_path if isinstance(binding_path, str) and binding_path.strip() else interface_prop
    parts = [p for p in path.split(".") if p]
    if not parts or len(parts) > 2:
        raise ValueError(
            f"interface property binding {path!r} must be a top-level name "
            f"or a one-level struct path (e.g. 'address.city')"
        )
    root = parts[0]
    if root not in property_mapping:
        raise ValueError(f"missing property {root!r} for interface binding {path!r}")
    if len(parts) == 1:
        return root, property_types.get(root)
    field = parts[1]
    root_rule = property_types.get(root)
    if not isinstance(root_rule, dict) or root_rule.get("kind") != "struct":
        raise ValueError(
            f"interface property binding {path!r} requires {root!r} to be a struct-typed property"
        )
    nested = root_rule.get("properties") or {}
    if field not in nested:
        raise ValueError(
            f"interface property binding {path!r}: struct {root!r} has no field {field!r}"
        )
    return root, nested.get(field)


async def validate_interface_property_types(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    required_properties: list[str],
    property_types: dict,
) -> dict[str, dict]:
    """Normalize + validate interface `property_types`. Keys must be in
    `required_properties`; each entry is a real value_type or
    shared_property_type leaf (no struct/array on the interface).
    """
    if not isinstance(property_types, dict):
        raise ValueError("property_types must be an object")

    from . import shared_property_types as shared_property_types_module
    from . import value_types as value_types_module

    required = set(required_properties)
    normalized: dict[str, dict] = {}
    for property_name, rule in property_types.items():
        if property_name not in required:
            raise ValueError(
                f"property_types entry {property_name!r} is not in required_properties"
            )
        if not isinstance(rule, dict):
            raise ValueError(f"property_types entry for {property_name!r} must be an object")
        kind = rule.get("kind")
        if kind not in _INTERFACE_PROPERTY_TYPE_KINDS:
            raise ValueError(
                f"property_types entry for {property_name!r} has unknown kind {kind!r} "
                f"(expected one of {sorted(_INTERFACE_PROPERTY_TYPE_KINDS)})"
            )
        if kind == "value_type":
            value_type_name = rule.get("value_type")
            if not isinstance(value_type_name, str) or not value_type_name:
                raise ValueError(
                    f"property_types entry for {property_name!r} requires value_type"
                )
            if await value_types_module.get_value_type(pool, tenant_id, value_type_name) is None:
                raise ValueError(
                    f"property_types entry for {property_name!r} names unknown "
                    f"value_type {value_type_name!r}"
                )
            normalized[property_name] = {"kind": "value_type", "value_type": value_type_name}
        else:
            spt_name = rule.get("shared_property_type")
            if not isinstance(spt_name, str) or not spt_name:
                raise ValueError(
                    f"property_types entry for {property_name!r} requires shared_property_type"
                )
            if await shared_property_types_module.get_shared_property_type(pool, tenant_id, spt_name) is None:
                raise ValueError(
                    f"property_types entry for {property_name!r} names unknown "
                    f"shared_property_type {spt_name!r}"
                )
            normalized[property_name] = {
                "kind": "shared_property_type",
                "shared_property_type": spt_name,
            }
    return normalized


def property_types_tighten(previous: dict, new: dict) -> bool:
    """True when typed bindings grow or change identity (breaking for
    implementers). Removing a binding is a relax.
    """
    previous = previous or {}
    new = new or {}
    for name, new_rule in new.items():
        new_key = property_type_binding_key(new_rule)
        if new_key is None:
            continue
        old_rule = previous.get(name)
        if old_rule is None:
            return True
        if property_type_binding_key(old_rule) != new_key:
            return True
    return False


_LINK_CARDINALITIES = frozenset({"one", "many"})
_LINK_TARGET_KINDS = frozenset({"object_type", "interface"})


def link_constraint_identity(constraint: dict) -> tuple:
    return (
        constraint.get("api_name"),
        constraint.get("target_kind"),
        constraint.get("target"),
        constraint.get("cardinality"),
        bool(constraint.get("required")),
    )


def link_constraints_tighten(previous: list, new: list) -> bool:
    """True when required constraints are added or an existing constraint's
    target/cardinality/requiredness changes in a breaking way.
    """
    prev_by_api = {
        c.get("api_name"): c for c in (previous or []) if isinstance(c, dict) and c.get("api_name")
    }
    for constraint in new or []:
        if not isinstance(constraint, dict):
            continue
        api_name = constraint.get("api_name")
        old = prev_by_api.get(api_name)
        if old is None:
            if constraint.get("required"):
                return True
            continue
        if link_constraint_identity(old) != link_constraint_identity(constraint):
            return True
    return False


async def validate_link_constraints(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    link_constraints: list,
) -> list[dict]:
    """Normalize interface link constraints (Foundry interface link types)."""
    if not isinstance(link_constraints, list):
        raise ValueError("link_constraints must be a list")

    from .object_types import list_object_types

    object_type_names = {ot["name"] for ot in await list_object_types(pool, tenant_id)}
    seen: set[str] = set()
    normalized: list[dict] = []
    for index, raw in enumerate(link_constraints):
        if not isinstance(raw, dict):
            raise ValueError(f"link_constraints[{index}] must be an object")
        api_name = raw.get("api_name")
        if not isinstance(api_name, str) or not api_name.strip():
            raise ValueError(f"link_constraints[{index}] requires api_name")
        api_name = api_name.strip()
        if api_name in seen:
            raise ValueError(f"duplicate link_constraints api_name {api_name!r}")
        seen.add(api_name)
        target_kind = raw.get("target_kind")
        if target_kind not in _LINK_TARGET_KINDS:
            raise ValueError(
                f"link_constraints[{api_name!r}] target_kind must be one of "
                f"{sorted(_LINK_TARGET_KINDS)}"
            )
        target = raw.get("target")
        if not isinstance(target, str) or not target.strip():
            raise ValueError(f"link_constraints[{api_name!r}] requires target")
        target = target.strip()
        if target_kind == "object_type":
            if target not in object_type_names:
                raise ValueError(
                    f"link_constraints[{api_name!r}] names unknown object_type {target!r}"
                )
        else:
            if await get_interface_type(pool, tenant_id, target) is None:
                raise ValueError(
                    f"link_constraints[{api_name!r}] names unknown interface {target!r}"
                )
        cardinality = raw.get("cardinality")
        if cardinality not in _LINK_CARDINALITIES:
            raise ValueError(
                f"link_constraints[{api_name!r}] cardinality must be one of "
                f"{sorted(_LINK_CARDINALITIES)}"
            )
        required = raw.get("required", False)
        if not isinstance(required, bool):
            raise ValueError(f"link_constraints[{api_name!r}] required must be a boolean")
        description = raw.get("description") or ""
        if not isinstance(description, str):
            raise ValueError(f"link_constraints[{api_name!r}] description must be a string")
        normalized.append(
            {
                "api_name": api_name,
                "target_kind": target_kind,
                "target": target,
                "cardinality": cardinality,
                "required": required,
                "description": description,
            }
        )
    return normalized


def outbound_cardinality_from_relation(relation: dict, implementer_name: str) -> Optional[str]:
    """Map a concrete RelationType to interface cardinality `one`/`many`
    looking outbound from the implementing ObjectType.
    """
    source = relation["source_object_type_urn"].rsplit(":", 1)[-1]
    target = relation["target_object_type_urn"].rsplit(":", 1)[-1]
    if implementer_name not in (source, target):
        return None
    card = relation.get("cardinality") or ""
    if implementer_name == source:
        return "one" if card in ("one_to_one", "many_to_one") else "many"
    return "one" if card in ("one_to_one", "one_to_many") else "many"


def relation_other_endpoint(relation: dict, implementer_name: str) -> Optional[str]:
    source = relation["source_object_type_urn"].rsplit(":", 1)[-1]
    target = relation["target_object_type_urn"].rsplit(":", 1)[-1]
    if implementer_name == source:
        return target
    if implementer_name == target:
        return source
    return None


async def validate_parent_interfaces(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    interface_name: str,
    parent_interfaces: list,
) -> list[str]:
    """Normalize parent list — known interfaces, no self, no cycles."""
    if not isinstance(parent_interfaces, list):
        raise ValueError("parent_interfaces must be a list")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in parent_interfaces:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("parent_interfaces entries must be non-empty strings")
        parent = raw.strip()
        if parent == interface_name:
            raise ValueError(f"interface {interface_name!r} cannot extend itself")
        if parent in seen:
            continue
        seen.add(parent)
        parent_row = await get_interface_type(pool, tenant_id, parent)
        if parent_row is None:
            raise ValueError(f"unknown parent interface: {parent!r}")
        # Cycle: if parent (transitively) already extends this interface.
        ancestors = await ancestor_interface_names(pool, tenant_id, parent)
        if interface_name in ancestors:
            raise ValueError(
                f"cycle detected: {parent!r} already extends {interface_name!r}"
            )
        normalized.append(parent)
    return normalized


async def ancestor_interface_names(
    pool: asyncpg.Pool, tenant_id: str, interface_name: str
) -> set[str]:
    """All transitive parents of `interface_name` (not including itself)."""
    ancestors: set[str] = set()
    stack = [interface_name]
    visited: set[str] = set()
    while stack:
        current_name = stack.pop()
        if current_name in visited:
            continue
        visited.add(current_name)
        row = await get_interface_type(pool, tenant_id, current_name)
        if row is None:
            continue
        for parent in row.get("parent_interfaces") or []:
            if parent not in ancestors:
                ancestors.add(parent)
                stack.append(parent)
    return ancestors


async def expand_implements(
    pool: asyncpg.Pool, tenant_id: str, implements: list[str]
) -> set[str]:
    """Direct implements plus every ancestor of those interfaces."""
    expanded: set[str] = set()
    for name in implements:
        expanded.add(name)
        expanded.update(await ancestor_interface_names(pool, tenant_id, name))
    return expanded


async def object_type_names_for_interface(
    pool: asyncpg.Pool, tenant_id: str, interface_name: str
) -> list[str]:
    """Published ObjectTypes whose implements expand to `interface_name`."""
    from .object_types import list_object_types

    names: list[str] = []
    for object_type in await list_object_types(pool, tenant_id):
        expanded = await expand_implements(
            pool, tenant_id, object_type.get("implements") or [],
        )
        if interface_name in expanded:
            names.append(object_type["name"])
    return names


async def descendant_interface_names(
    pool: asyncpg.Pool, tenant_id: str, interface_name: str
) -> set[str]:
    """Interfaces that transitively extend `interface_name`."""
    descendants: set[str] = set()
    for iface in await list_interface_types(pool, tenant_id):
        if iface["name"] == interface_name:
            continue
        if interface_name in await ancestor_interface_names(pool, tenant_id, iface["name"]):
            descendants.add(iface["name"])
    return descendants


def _merge_property_types(base: dict, overlay: dict, *, context: str) -> dict:
    merged = dict(base)
    for key, rule in overlay.items():
        if key in merged and property_type_binding_key(merged[key]) != property_type_binding_key(rule):
            raise ValueError(
                f"{context}: conflicting property_types for {key!r} "
                f"({property_type_binding_key(merged[key])} vs {property_type_binding_key(rule)})"
            )
        merged[key] = rule
    return merged


def _merge_link_constraints(base: list, overlay: list, *, context: str) -> list:
    by_api = {c["api_name"]: c for c in base}
    for constraint in overlay:
        api_name = constraint["api_name"]
        if api_name in by_api and link_constraint_identity(by_api[api_name]) != link_constraint_identity(constraint):
            raise ValueError(
                f"{context}: conflicting link_constraints for api_name {api_name!r}"
            )
        by_api[api_name] = constraint
    return list(by_api.values())


async def effective_interface_contract(
    pool: asyncpg.Pool,
    tenant_id: str,
    interface_name: str,
    *,
    override: Optional[dict] = None,
    overrides: Optional[dict[str, dict]] = None,
) -> dict:
    """Merged contract for `interface_name`: local fields overlay parents
    (child wins on same property/link api_name). `override` / `overrides`
    substitute row(s) pre-write during tighten checks.
    """
    override_map = dict(overrides or {})
    if override is not None:
        override_map[interface_name] = override
    local = override_map.get(interface_name)
    if local is None:
        local = await get_interface_type(pool, tenant_id, interface_name)
    if local is None:
        raise ValueError(f"unknown interface: {interface_name!r}")

    parents = list(local.get("parent_interfaces") or [])
    required_properties: list[str] = []
    required_actions: list[str] = []
    property_types: dict = {}
    link_constraints: list = []
    seen_props: set[str] = set()
    seen_actions: set[str] = set()

    for parent in parents:
        parent_effective = await effective_interface_contract(
            pool, tenant_id, parent, overrides=override_map,
        )
        for prop in parent_effective["required_properties"]:
            if prop not in seen_props:
                seen_props.add(prop)
                required_properties.append(prop)
        for action in parent_effective["required_actions"]:
            if action not in seen_actions:
                seen_actions.add(action)
                required_actions.append(action)
        property_types = _merge_property_types(
            property_types,
            parent_effective.get("property_types") or {},
            context=f"extending {parent!r} into {interface_name!r}",
        )
        link_constraints = _merge_link_constraints(
            link_constraints,
            parent_effective.get("link_constraints") or [],
            context=f"extending {parent!r} into {interface_name!r}",
        )

    for prop in local.get("required_properties") or []:
        if prop not in seen_props:
            seen_props.add(prop)
            required_properties.append(prop)
    for action in local.get("required_actions") or []:
        if action not in seen_actions:
            seen_actions.add(action)
            required_actions.append(action)
    property_types = _merge_property_types(
        property_types,
        local.get("property_types") or {},
        context=f"interface {interface_name!r}",
    )
    link_constraints = _merge_link_constraints(
        link_constraints,
        local.get("link_constraints") or [],
        context=f"interface {interface_name!r}",
    )

    return {
        "name": interface_name,
        "parent_interfaces": parents,
        "required_properties": required_properties,
        "required_actions": required_actions,
        "property_types": property_types,
        "link_constraints": link_constraints,
    }


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
