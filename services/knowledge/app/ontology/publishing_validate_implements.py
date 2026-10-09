"""Implements / interface-tighten checks at ObjectType publish time."""
from __future__ import annotations

from typing import Optional

import asyncpg


def _action_local_name(action_name: str) -> str:
    """Interface `required_actions` store the short local name
    (e.g. `archive`); registry keys are usually `Type.action`.
    Bare names (no dot) pass through unchanged.
    """
    return action_name.split(".", 1)[1] if "." in action_name else action_name

async def _actions_available_on_object_type(
    pool: asyncpg.Pool, *, tenant_id: str, object_type_name: str, implements: list[str]
) -> set[str]:
    """Short action names invokable on this ObjectType after publish —
    declarative ActionTypes targeting the OT directly, and ActionTypes
    targeting any interface this version declares in `implements`
    (Actions-on-interfaces).
    """
    from .action_types import list_action_types

    implements_set = set(implements)
    names: set[str] = set()

    for action_type in await list_action_types(pool, tenant_id):
        if action_type.get("target_object_type") == object_type_name:
            names.add(_action_local_name(action_type["name"]))
        target_interface = action_type.get("target_interface")
        if target_interface and target_interface in implements_set:
            names.add(_action_local_name(action_type["name"]))
    return names

async def _validate_implements(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    object_type_name: str,
    property_mapping: dict,
    implements: list[str],
    property_types: Optional[dict[str, dict]] = None,
    link_constraint_bindings: Optional[dict] = None,
    interface_property_bindings: Optional[dict] = None,
    interface_overrides: Optional[dict[str, dict]] = None,
) -> None:
    """Validate that the version satisfies all declared interface contracts."""
    from .interfaces import (
        effective_interface_contract,
        expand_implements,
        outbound_cardinality_from_relation,
        property_type_binding_key,
        relation_other_endpoint,
        resolve_interface_property_path,
    )
    from .object_types import list_object_types
    from .relation_types import list_relation_types

    expanded_implements = await expand_implements(pool, tenant_id, implements)
    object_action_names = await _actions_available_on_object_type(
        pool,
        tenant_id=tenant_id,
        object_type_name=object_type_name,
        implements=list(expanded_implements),
    )
    ot_property_types = property_types or {}
    bindings_by_interface = link_constraint_bindings or {}
    prop_bindings_by_interface = interface_property_bindings or {}
    relations = await list_relation_types(pool, tenant_id)
    relations_by_name = {rel["name"]: rel for rel in relations}
    object_types_by_name = {ot["name"]: ot for ot in await list_object_types(pool, tenant_id)}

    for interface_name in implements:
        interface = await effective_interface_contract(
            pool, tenant_id, interface_name, overrides=interface_overrides,
        )
        prop_bindings = prop_bindings_by_interface.get(interface_name) or {}
        if prop_bindings and not isinstance(prop_bindings, dict):
            raise ValueError(
                f"{object_type_name} cannot implement {interface_name!r}: "
                f"interface_property_bindings[{interface_name!r}] must be an object"
            )
        missing_properties: list[str] = []
        for prop_name in interface["required_properties"]:
            try:
                resolve_interface_property_path(
                    property_mapping=property_mapping,
                    property_types=ot_property_types,
                    interface_prop=prop_name,
                    binding_path=prop_bindings.get(prop_name),
                )
            except ValueError:
                missing_properties.append(prop_name)
        if missing_properties:
            raise ValueError(
                f"{object_type_name} cannot implement {interface_name!r}: "
                f"missing required propert{'y' if len(missing_properties) == 1 else 'ies'} {missing_properties}"
            )
        missing_actions = [a for a in interface["required_actions"] if a not in object_action_names]
        if missing_actions:
            raise ValueError(
                f"{object_type_name} cannot implement {interface_name!r}: "
                f"missing required action{'s' if len(missing_actions) != 1 else ''} {missing_actions}"
            )
        for prop_name, iface_rule in (interface.get("property_types") or {}).items():
            expected = property_type_binding_key(iface_rule)
            if expected is None:
                continue
            try:
                _, ot_rule = resolve_interface_property_path(
                    property_mapping=property_mapping,
                    property_types=ot_property_types,
                    interface_prop=prop_name,
                    binding_path=prop_bindings.get(prop_name),
                )
            except ValueError as exc:
                raise ValueError(
                    f"{object_type_name} cannot implement {interface_name!r}: {exc}"
                ) from exc
            actual = property_type_binding_key(ot_rule) if ot_rule is not None else None
            if actual != expected:
                kind, ref = expected
                raise ValueError(
                    f"{object_type_name} cannot implement {interface_name!r}: "
                    f"property {prop_name!r} must be typed as {kind} {ref!r} "
                    f"(got {actual!r})"
                )

        iface_bindings = bindings_by_interface.get(interface_name) or {}
        if not isinstance(iface_bindings, dict):
            raise ValueError(
                f"{object_type_name} cannot implement {interface_name!r}: "
                f"link_constraint_bindings[{interface_name!r}] must be an object"
            )
        for constraint in interface.get("link_constraints") or []:
            api_name = constraint["api_name"]
            relation_name = iface_bindings.get(api_name)
            if not relation_name:
                if constraint.get("required"):
                    raise ValueError(
                        f"{object_type_name} cannot implement {interface_name!r}: "
                        f"missing required link binding for {api_name!r}"
                    )
                continue
            relation = relations_by_name.get(relation_name)
            if relation is None:
                raise ValueError(
                    f"{object_type_name} cannot implement {interface_name!r}: "
                    f"link {api_name!r} binds unknown RelationType {relation_name!r}"
                )
            outbound = outbound_cardinality_from_relation(relation, object_type_name)
            if outbound is None:
                raise ValueError(
                    f"{object_type_name} cannot implement {interface_name!r}: "
                    f"RelationType {relation_name!r} does not touch this ObjectType"
                )
            if outbound != constraint["cardinality"]:
                raise ValueError(
                    f"{object_type_name} cannot implement {interface_name!r}: "
                    f"link {api_name!r} requires cardinality {constraint['cardinality']!r}, "
                    f"RelationType {relation_name!r} is outbound {outbound!r}"
                )
            other = relation_other_endpoint(relation, object_type_name)
            if constraint["target_kind"] == "object_type":
                if other != constraint["target"]:
                    raise ValueError(
                        f"{object_type_name} cannot implement {interface_name!r}: "
                        f"link {api_name!r} requires target ObjectType {constraint['target']!r}, "
                        f"got {other!r}"
                    )
            else:
                other_ot = object_types_by_name.get(other or "")
                other_implements = await expand_implements(
                    pool, tenant_id, (other_ot or {}).get("implements") or [],
                )
                if constraint["target"] not in other_implements:
                    raise ValueError(
                        f"{object_type_name} cannot implement {interface_name!r}: "
                        f"link {api_name!r} requires target interface {constraint['target']!r}, "
                        f"but {other!r} does not implement it"
                    )

async def assert_interface_tighten_compatible(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    interface_name: str,
    previous_properties: list[str],
    previous_actions: list[str],
    new_properties: list[str],
    new_actions: list[str],
    previous_property_types: Optional[dict] = None,
    new_property_types: Optional[dict] = None,
    previous_link_constraints: Optional[list] = None,
    new_link_constraints: Optional[list] = None,
    previous_parent_interfaces: Optional[list] = None,
    new_parent_interfaces: Optional[list] = None,
) -> None:
    """Refuse edits that would break published implementers of this
    interface or of any child that extends it (effective contract).
    """
    from .interfaces import (
        ancestor_interface_names,
        link_constraints_tighten,
        property_types_tighten,
    )
    from .object_types import list_object_types

    added_properties = set(new_properties) - set(previous_properties)
    added_actions = set(new_actions) - set(previous_actions)
    types_tighten = property_types_tighten(
        previous_property_types or {}, new_property_types or {}
    )
    links_tighten = link_constraints_tighten(
        previous_link_constraints or [], new_link_constraints or []
    )
    prev_parents = list(previous_parent_interfaces or [])
    next_parents = list(new_parent_interfaces or [])
    parents_tighten = set(next_parents) - set(prev_parents)
    if (
        not added_properties
        and not added_actions
        and not types_tighten
        and not links_tighten
        and not parents_tighten
    ):
        return

    proposed = {
        "parent_interfaces": next_parents,
        "required_properties": new_properties,
        "required_actions": new_actions,
        "property_types": new_property_types or {},
        "link_constraints": new_link_constraints or [],
    }
    overrides = {interface_name: proposed}

    failures: list[str] = []
    for object_type in await list_object_types(pool, tenant_id):
        implements = object_type.get("implements") or []
        affected = False
        for impl in implements:
            if impl == interface_name:
                affected = True
                break
            if interface_name in await ancestor_interface_names(pool, tenant_id, impl):
                affected = True
                break
        if not affected:
            continue
        try:
            await _validate_implements(
                pool,
                tenant_id=tenant_id,
                object_type_name=object_type["name"],
                property_mapping=object_type.get("property_mapping") or {},
                implements=implements,
                property_types=object_type.get("property_types") or {},
                link_constraint_bindings=object_type.get("link_constraint_bindings") or {},
                interface_property_bindings=object_type.get("interface_property_bindings") or {},
                interface_overrides=overrides,
            )
        except ValueError as exc:
            failures.append(str(exc))

    if failures:
        raise ValueError(
            f"cannot tighten interface {interface_name!r}: "
            f"{len(failures)} implementer(s) would break — " + "; ".join(failures)
        )

