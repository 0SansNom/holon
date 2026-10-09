"""Detach Shared Property Types from ObjectType property_types on delete."""
from __future__ import annotations

import json
from typing import Any, Optional

import asyncpg


def _property_types_reference_spt(property_types: Any, api_name: str) -> bool:
    """True if any top-level or nested leaf names this SPT."""
    if not isinstance(property_types, dict):
        return False
    for rule in property_types.values():
        if not isinstance(rule, dict):
            continue
        if rule.get("kind") == "shared_property_type" and rule.get("shared_property_type") == api_name:
            return True
        if rule.get("kind") == "struct":
            for leaf in (rule.get("properties") or {}).values():
                if isinstance(leaf, dict) and leaf.get("kind") == "shared_property_type" and leaf.get("shared_property_type") == api_name:
                    return True
        if rule.get("kind") == "array":
            element = rule.get("element") or {}
            if isinstance(element, dict):
                if element.get("kind") == "shared_property_type" and element.get("shared_property_type") == api_name:
                    return True
                if element.get("kind") == "struct":
                    for leaf in (element.get("properties") or {}).values():
                        if (
                            isinstance(leaf, dict)
                            and leaf.get("kind") == "shared_property_type"
                            and leaf.get("shared_property_type") == api_name
                        ):
                            return True
    return False

def _local_rule_from_spt(spt: dict) -> dict:
    """Convert SPT definition into local property type rule."""
    rule: dict[str, Any]
    if isinstance(spt.get("struct_properties"), dict) and spt["struct_properties"]:
        rule = {"kind": "struct", "properties": dict(spt["struct_properties"])}
    elif spt.get("value_type"):
        rule = {"kind": "value_type", "value_type": spt["value_type"]}
    else:
        raise ValueError(f"shared property type {spt.get('api_name')!r} has no value_type or struct_properties")
    if spt.get("visibility") and spt["visibility"] != "normal":
        rule["visibility"] = spt["visibility"]
    hints = spt.get("render_hints")
    if isinstance(hints, list) and hints != ["searchable"]:
        rule["render_hints"] = list(hints)
    classes = spt.get("type_classes")
    if isinstance(classes, list) and classes:
        rule["type_classes"] = list(classes)
    return rule

def _detach_spt_from_rule(rule: dict, api_name: str, spt: dict, *, leaf_resolver: dict[str, dict]) -> dict:
    """Rewrite one property_types rule, replacing references to `api_name`."""
    if not isinstance(rule, dict):
        return rule
    kind = rule.get("kind")
    if kind == "shared_property_type" and rule.get("shared_property_type") == api_name:
        return _local_rule_from_spt(spt)
    if kind == "struct":
        properties = rule.get("properties") or {}
        if not isinstance(properties, dict):
            return rule
        next_props = {}
        for field_name, leaf in properties.items():
            if (
                isinstance(leaf, dict)
                and leaf.get("kind") == "shared_property_type"
                and leaf.get("shared_property_type") == api_name
            ):
                # Nested SPT leaves are always value-typed — enforced at
                # publish time (`publishing._validate_property_types`), a
                # struct-typed SPT can never legally be nested inside
                # another struct. Fail loudly rather than silently collapse
                # a struct-shaped field to a bogus string if that
                # invariant is somehow violated (pre-existing data from
                # before that check existed, for instance).
                nested = leaf_resolver.get(api_name) or spt
                if not nested.get("value_type"):
                    raise ValueError(
                        f"shared property type {api_name!r} is struct-typed but is nested as a leaf "
                        f"in field {field_name!r} — this should be unreachable (struct-in-struct is "
                        f"rejected at publish time); refusing to silently corrupt the field's type"
                    )
                next_leaf: dict[str, Any] = {"kind": "value_type", "value_type": nested["value_type"]}
                if leaf.get("description"):
                    next_leaf["description"] = leaf["description"]
                if leaf.get("main_field"):
                    next_leaf["main_field"] = True
                next_props[field_name] = next_leaf
            else:
                next_props[field_name] = leaf
        return {**rule, "properties": next_props}
    if kind == "array":
        element = rule.get("element")
        if isinstance(element, dict):
            return {**rule, "element": _detach_spt_from_rule(element, api_name, spt, leaf_resolver=leaf_resolver)}
    return rule

def _detach_spt_from_property_types(
    property_types: dict, api_name: str, spt: dict
) -> dict:
    leaf_resolver = {api_name: spt}
    return {
        name: _detach_spt_from_rule(rule, api_name, spt, leaf_resolver=leaf_resolver)
        for name, rule in property_types.items()
    }

async def _apply_detach_to_object_type(
    conn: asyncpg.Connection, *, tenant_id: str, object_type_name: str, api_name: str, spt: dict
) -> None:
    row = await conn.fetchrow(
        "SELECT property_types, property_formats FROM object_type WHERE tenant_id = $1 AND name = $2",
        tenant_id,
        object_type_name,
    )
    if row is None:
        return
    property_types = row["property_types"]
    if isinstance(property_types, str):
        property_types = json.loads(property_types)
    property_types = property_types or {}
    if not _property_types_reference_spt(property_types, api_name):
        return
    rewritten = _detach_spt_from_property_types(property_types, api_name, spt)

    property_formats = row["property_formats"]
    if isinstance(property_formats, str):
        property_formats = json.loads(property_formats)
    property_formats = dict(property_formats or {})
    fmt = spt.get("property_format")
    if isinstance(fmt, dict):
        for prop_name, rule in property_types.items():
            if (
                isinstance(rule, dict)
                and rule.get("kind") == "shared_property_type"
                and rule.get("shared_property_type") == api_name
                and prop_name not in property_formats
            ):
                property_formats[prop_name] = fmt

    await conn.execute(
        """
        UPDATE object_type
        SET property_types = $1::jsonb, property_formats = $2::jsonb
        WHERE tenant_id = $3 AND name = $4
        """,
        json.dumps(rewritten),
        json.dumps(property_formats),
        tenant_id,
        object_type_name,
    )
    # Keep the latest draft version in sync when it still references the SPT.
    draft = await conn.fetchrow(
        """
        SELECT version, property_types, property_formats
        FROM object_type_version
        WHERE tenant_id = $1 AND name = $2 AND status = 'draft'
        ORDER BY version DESC
        LIMIT 1
        """,
        tenant_id,
        object_type_name,
    )
    if draft is None:
        return
    draft_types = draft["property_types"]
    if isinstance(draft_types, str):
        draft_types = json.loads(draft_types)
    draft_types = draft_types or {}
    if not _property_types_reference_spt(draft_types, api_name):
        return
    draft_rewritten = _detach_spt_from_property_types(draft_types, api_name, spt)
    draft_formats = draft["property_formats"]
    if isinstance(draft_formats, str):
        draft_formats = json.loads(draft_formats)
    draft_formats = dict(draft_formats or {})
    if isinstance(fmt, dict):
        for prop_name, rule in draft_types.items():
            if (
                isinstance(rule, dict)
                and rule.get("kind") == "shared_property_type"
                and rule.get("shared_property_type") == api_name
                and prop_name not in draft_formats
            ):
                draft_formats[prop_name] = fmt
    await conn.execute(
        """
        UPDATE object_type_version
        SET property_types = $1::jsonb, property_formats = $2::jsonb
        WHERE tenant_id = $3 AND name = $4 AND version = $5
        """,
        json.dumps(draft_rewritten),
        json.dumps(draft_formats),
        tenant_id,
        object_type_name,
        draft["version"],
    )

