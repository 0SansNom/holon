"""Derived-property structural checks at ObjectType publish time."""
from __future__ import annotations

from typing import Optional

import asyncpg

from .object_types import get_object_type


_ALLOWED_AGGREGATES = {"sum", "count", "avg", "min", "max", "collect_list", "collect_set"}
_ALLOWED_STRUCT_REDUCERS = {"first", "last", "latest", "earliest", "max", "min"}
_FIELD_BASED_STRUCT_REDUCERS = {"latest", "earliest", "max", "min"}
_MAX_LINK_AGGREGATE_HOPS = 3

def _find_relation_by_link_name(relation_types: list[dict], object_type_name: str, link_name: object) -> Optional[dict]:
    """Pure structural lookup — same matching `core._find_relation_by_link_name`
    does at read/traversal time, duplicated here rather than imported
    because `ontology/` never depends on the app-layer `core` module (the
    reverse dependency direction every other cross-layer boundary in this
    build already keeps). No live traversal at publish time, just "does a
    RelationType named this exist, touching this ObjectType" — the same
    split every other real-reference check in this function already has.
    """
    for relation in relation_types:
        source_name = relation["source_object_type_urn"].rsplit(":", 1)[-1]
        target_name = relation["target_object_type_urn"].rsplit(":", 1)[-1]
        local_name = relation["name"].split(".", 1)[-1]
        forward = (relation.get("source_api_name") or "").strip() or local_name
        reverse = (relation.get("target_api_name") or "").strip() or relation.get("target_property")
        if source_name == object_type_name and forward == link_name:
            return relation
        if target_name == object_type_name and reverse == link_name:
            return relation
        if source_name == object_type_name and local_name == link_name:
            return relation
        if target_name == object_type_name and relation.get("target_property") == link_name:
            return relation
    return None

def _link_aggregate_path(rule: dict) -> list[str]:
    """Return list of link names along multi-hop path."""
    path = rule.get("path")
    return path if isinstance(path, list) else []

async def _validate_derived_properties(
    pool: asyncpg.Pool, *, derived_properties: dict[str, object], object_type_name: str, tenant_id: str,
    property_types: Optional[dict[str, dict]] = None,
) -> None:
    """Validate derived property definitions, aggregations, and reducers."""
    from .. import function_registry
    from .relation_types import list_relation_types

    property_types = property_types or {}
    relation_types: Optional[list[dict]] = None
    for property_name, value in derived_properties.items():
        if isinstance(value, str):
            plugin = await function_registry.find_active_function_by_name(pool, value)
            if plugin is None:
                raise ValueError(
                    f"derived property {property_name!r} names {value!r}, "
                    f"which is not a registered, active Function plugin"
                )
            continue

        if not isinstance(value, dict) or value.get("kind") not in ("link_aggregate", "struct_reducer"):
            raise ValueError(
                f"derived property {property_name!r} must be either a Function plugin name (string), "
                f"a {{'kind': 'link_aggregate', ...}} object, or a {{'kind': 'struct_reducer', ...}} object"
            )

        if value["kind"] == "struct_reducer":
            array_property = value.get("property")
            array_rule = property_types.get(array_property) if array_property else None
            if array_rule is None or array_rule.get("kind") != "array":
                raise ValueError(
                    f"derived property {property_name!r} names {array_property!r}, which isn't an 'array'-kind "
                    f"property_types entry on this ObjectType"
                )
            reducer = value.get("reducer")
            if reducer not in _ALLOWED_STRUCT_REDUCERS:
                raise ValueError(
                    f"derived property {property_name!r}: unknown reducer {reducer!r} "
                    f"(expected one of {sorted(_ALLOWED_STRUCT_REDUCERS)})"
                )
            element_rule = array_rule.get("element") or {}
            by = value.get("by")
            if reducer in _FIELD_BASED_STRUCT_REDUCERS:
                if element_rule.get("kind") == "struct":
                    if not by or by not in (element_rule.get("properties") or {}):
                        raise ValueError(
                            f"derived property {property_name!r}: reducer {reducer!r} on a struct array requires "
                            f"'by' to name one of the struct's own fields"
                        )
                elif by is not None:
                    raise ValueError(
                        f"derived property {property_name!r}: reducer {reducer!r} on a scalar array must not set "
                        f"'by' — there's nothing to key by, the values are compared directly"
                    )
            continue

        aggregate = value.get("aggregate")
        if aggregate not in _ALLOWED_AGGREGATES:
            raise ValueError(
                f"derived property {property_name!r}: unknown aggregate {aggregate!r} "
                f"(expected one of {sorted(_ALLOWED_AGGREGATES)})"
            )
        if "collect_limit" in value:
            limit = value["collect_limit"]
            if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
                raise ValueError(
                    f"derived property {property_name!r}: collect_limit must be a positive integer"
                )
        path = _link_aggregate_path(value)
        if (
            not path
            or len(path) > _MAX_LINK_AGGREGATE_HOPS
            or not all(isinstance(hop, str) and hop for hop in path)
        ):
            raise ValueError(
                f"derived property {property_name!r}: link_aggregate requires 'path' "
                f"(1–{_MAX_LINK_AGGREGATE_HOPS} link names)"
            )
        if relation_types is None:
            relation_types = await list_relation_types(pool, tenant_id)
        current_type = object_type_name
        related_urn: Optional[str] = None
        for hop in path:
            relation = _find_relation_by_link_name(relation_types, current_type, hop)
            if relation is None:
                raise ValueError(
                    f"derived property {property_name!r} names unknown relation {hop!r} "
                    f"from ObjectType {current_type!r}"
                )
            source_name = relation["source_object_type_urn"].rsplit(":", 1)[-1]
            if source_name == current_type:
                related_urn = relation["target_object_type_urn"]
            else:
                related_urn = relation["source_object_type_urn"]
            current_type = related_urn.rsplit(":", 1)[-1]
        if aggregate != "count":
            related_property = value.get("property")
            if not related_property:
                raise ValueError(f"derived property {property_name!r}: aggregate {aggregate!r} requires a 'property'")
            assert related_urn is not None
            related_definition = await get_object_type(pool, related_urn)
            if related_definition is None or related_property not in related_definition["property_mapping"]:
                raise ValueError(
                    f"derived property {property_name!r}: {related_property!r} is not a mapped property "
                    f"on the related ObjectType"
                )

