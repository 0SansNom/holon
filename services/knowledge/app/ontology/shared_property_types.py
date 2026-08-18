"""Shared Property Type registry — canonical, reusable property definitions."""

from __future__ import annotations

import json
from typing import Any, Optional

import asyncpg
from holon_common import build_urn

from . import value_types as value_types_module
from .type_classes import normalize_type_classes
from .render_hints import normalize_render_hints


_ALLOWED_VISIBILITY = frozenset({"prominent", "normal", "hidden"})


def shared_property_type_urn(tenant_id: str, api_name: str) -> str:
    """Stable Holon RID equivalent — `hl:{tenant}:global:shared-property-type:{api_name}`."""
    return build_urn(tenant_id, "global", "shared-property-type", api_name)


def _row_to_dict(row: asyncpg.Record) -> dict:
    data = dict(row)
    for key in ("struct_properties", "render_hints", "type_classes", "property_format", "aliases"):
        raw = data.get(key)
        if isinstance(raw, str):
            data[key] = json.loads(raw)
    data["urn"] = shared_property_type_urn(data["tenant_id"], data["api_name"])
    return data


def _validate_struct_properties(struct_properties: dict[str, Any]) -> None:
    if not struct_properties:
        raise ValueError("struct_properties must be a non-empty object")
    for field_name, rule in struct_properties.items():
        if not isinstance(field_name, str) or not field_name.strip():
            raise ValueError("struct field names must be non-empty strings")
        if not isinstance(rule, dict) or rule.get("kind") not in ("value_type", "shared_property_type"):
            raise ValueError(
                f"struct field {field_name!r} must be a value_type or shared_property_type leaf"
            )
        if "description" in rule and not isinstance(rule["description"], str):
            raise ValueError(f"struct field {field_name!r}: description must be a string")
        if "main_field" in rule and not isinstance(rule["main_field"], bool):
            raise ValueError(f"struct field {field_name!r}: main_field must be a boolean")
        if "column" in rule:
            raise ValueError(
                f"struct field {field_name!r}: column mapping belongs on ObjectType property_types, "
                f"not on a Shared Property Type definition"
            )


def _normalize_metadata(
    *,
    visibility: str = "normal",
    render_hints: Optional[list[str]] = None,
    type_classes: Optional[list[str]] = None,
    property_format: Optional[dict] = None,
) -> tuple[str, list[str], list[str], Optional[dict]]:
    if visibility not in _ALLOWED_VISIBILITY:
        raise ValueError(f"visibility must be one of {sorted(_ALLOWED_VISIBILITY)}")
    hints = normalize_render_hints(render_hints, default=["searchable"])
    classes = normalize_type_classes(type_classes)
    if property_format is not None and not isinstance(property_format, dict):
        raise ValueError("property_format must be an object")
    return visibility, hints, classes, property_format


def _normalize_aliases(aliases: Optional[list[str]]) -> list[str]:
    """Foundry aliases — alternate search terms; de-dupe case-insensitively."""
    if aliases is None:
        return []
    if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
        raise ValueError("aliases must be a list of strings")
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in aliases:
        term = raw.strip()
        if not term:
            continue
        if len(term) > 128:
            raise ValueError(f"alias too long ({len(term)} > 128): {term!r}")
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(term)
    return cleaned


async def create_shared_property_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    api_name: str,
    display_name: str,
    value_type: Optional[str] = None,
    struct_properties: Optional[dict[str, Any]] = None,
    description: str = "",
    visibility: str = "normal",
    render_hints: Optional[list[str]] = None,
    type_classes: Optional[list[str]] = None,
    property_format: Optional[dict] = None,
    aliases: Optional[list[str]] = None,
    project_urn: Optional[str] = None,
) -> dict:
    if struct_properties is not None and value_type:
        raise ValueError("provide either value_type or struct_properties, not both")
    visibility, hints, classes, fmt = _normalize_metadata(
        visibility=visibility,
        render_hints=render_hints,
        type_classes=type_classes,
        property_format=property_format,
    )
    alias_list = _normalize_aliases(aliases)
    scoped_project = project_urn.strip() if isinstance(project_urn, str) and project_urn.strip() else None

    if struct_properties is not None:
        if not isinstance(struct_properties, dict):
            raise ValueError("struct_properties must be an object")
        _validate_struct_properties(struct_properties)
        for field_name, rule in struct_properties.items():
            if rule["kind"] == "value_type":
                if await value_types_module.get_value_type(pool, tenant_id, rule.get("value_type")) is None:
                    raise ValueError(
                        f"struct field {field_name!r} names unknown value_type {rule.get('value_type')!r}"
                    )
            else:
                leaf_spt = rule.get("shared_property_type")
                nested = await get_shared_property_type(pool, tenant_id, leaf_spt)
                if nested is None:
                    raise ValueError(
                        f"struct field {field_name!r} names unknown shared_property_type {leaf_spt!r}"
                    )
                if nested.get("struct_properties"):
                    raise ValueError(
                        f"struct field {field_name!r}: cannot nest a struct-typed shared property"
                    )
        await pool.execute(
            """
            INSERT INTO shared_property_type
                (tenant_id, api_name, display_name, value_type, struct_properties, description,
                 visibility, render_hints, type_classes, property_format, aliases, project_urn)
            VALUES ($1, $2, $3, NULL, $4::jsonb, $5, $6, $7::jsonb, $8::jsonb, $9::jsonb, $10::jsonb, $11)
            """,
            tenant_id,
            api_name,
            display_name,
            json.dumps(struct_properties),
            description,
            visibility,
            json.dumps(hints),
            json.dumps(classes),
            json.dumps(fmt) if fmt is not None else None,
            json.dumps(alias_list),
            scoped_project,
        )
    else:
        if not value_type:
            raise ValueError("value_type is required unless struct_properties is provided")
        if await value_types_module.get_value_type(pool, tenant_id, value_type) is None:
            raise ValueError(f"unknown value_type: {value_type!r}")
        await pool.execute(
            """
            INSERT INTO shared_property_type
                (tenant_id, api_name, display_name, value_type, struct_properties, description,
                 visibility, render_hints, type_classes, property_format, aliases, project_urn)
            VALUES ($1, $2, $3, $4, NULL, $5, $6, $7::jsonb, $8::jsonb, $9::jsonb, $10::jsonb, $11)
            """,
            tenant_id,
            api_name,
            display_name,
            value_type,
            description,
            visibility,
            json.dumps(hints),
            json.dumps(classes),
            json.dumps(fmt) if fmt is not None else None,
            json.dumps(alias_list),
            scoped_project,
        )
    return await get_shared_property_type(pool, tenant_id, api_name)


async def update_shared_property_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    api_name: str,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    visibility: Optional[str] = None,
    render_hints: Optional[list[str]] = None,
    type_classes: Optional[list[str]] = None,
    property_format: Optional[dict] = None,
    clear_property_format: bool = False,
    aliases: Optional[list[str]] = None,
    project_urn: Optional[str] = None,
    clear_project_urn: bool = False,
) -> dict:
    """Partial update — `value_type` / `struct_properties` / `api_name`
    are deliberately not accepted: changing the data contract would
    silently break every property referencing this SPT. `None` means
    leave unchanged; `clear_property_format=True` clears format;
    `clear_project_urn=True` unscopes the SPT.
    """
    current = await get_shared_property_type(pool, tenant_id, api_name)
    if current is None:
        raise ValueError(f"unknown shared property type: {api_name!r}")

    new_display_name = current["display_name"] if display_name is None else display_name
    new_description = current["description"] if description is None else description
    new_visibility = (current.get("visibility") or "normal") if visibility is None else visibility
    new_hints = current.get("render_hints") if render_hints is None else render_hints
    new_classes = current.get("type_classes") if type_classes is None else type_classes
    if clear_property_format:
        new_format: Optional[dict] = None
    elif property_format is not None:
        new_format = property_format
    else:
        new_format = current.get("property_format")
    new_aliases = current.get("aliases") if aliases is None else aliases
    if clear_project_urn:
        new_project_urn: Optional[str] = None
    elif project_urn is not None:
        new_project_urn = project_urn.strip() or None
    else:
        new_project_urn = current.get("project_urn")

    new_visibility, new_hints, new_classes, new_format = _normalize_metadata(
        visibility=new_visibility,
        render_hints=new_hints if isinstance(new_hints, list) else ["searchable"],
        type_classes=new_classes if isinstance(new_classes, list) else [],
        property_format=new_format,
    )
    alias_list = _normalize_aliases(new_aliases if isinstance(new_aliases, list) else [])

    await pool.execute(
        """
        UPDATE shared_property_type
        SET display_name = $1, description = $2, visibility = $3,
            render_hints = $4::jsonb, type_classes = $5::jsonb, property_format = $6::jsonb,
            aliases = $7::jsonb, project_urn = $8
        WHERE tenant_id = $9 AND api_name = $10
        """,
        new_display_name,
        new_description,
        new_visibility,
        json.dumps(new_hints),
        json.dumps(new_classes),
        json.dumps(new_format) if new_format is not None else None,
        json.dumps(alias_list),
        new_project_urn,
        tenant_id,
        api_name,
    )
    return await get_shared_property_type(pool, tenant_id, api_name)


async def get_shared_property_type(pool: asyncpg.Pool, tenant_id: str, api_name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        "SELECT * FROM shared_property_type WHERE tenant_id = $1 AND api_name = $2", tenant_id, api_name
    )
    return _row_to_dict(row) if row else None


async def list_shared_property_types(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM shared_property_type WHERE tenant_id = $1 ORDER BY api_name", tenant_id)
    return [_row_to_dict(row) for row in rows]


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


async def list_shared_property_type_usage(pool: asyncpg.Pool, tenant_id: str, api_name: str) -> list[dict]:
    """ObjectTypes (live) that reference this SPT in property_types."""
    if await get_shared_property_type(pool, tenant_id, api_name) is None:
        raise ValueError(f"unknown shared property type: {api_name!r}")
    rows = await pool.fetch(
        """
        SELECT name, property_types
        FROM object_type
        WHERE tenant_id = $1
        ORDER BY name
        """,
        tenant_id,
    )
    usage: list[dict] = []
    for row in rows:
        raw = row["property_types"]
        property_types = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if _property_types_reference_spt(property_types, api_name):
            usage.append({"object_type": row["name"]})
    return usage


def _local_rule_from_spt(spt: dict) -> dict:
    """Foundry delete-revert: SPT → regular local property type rule."""
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


async def delete_shared_property_type(
    pool: asyncpg.Pool, *, tenant_id: str, api_name: str
) -> dict:
    """Foundry parity: deleting a shared property reverts attached
    ObjectType properties to regular (local) types, then removes the SPT.
    """
    current = await get_shared_property_type(pool, tenant_id, api_name)
    if current is None:
        raise ValueError(f"unknown shared property type: {api_name!r}")
    usage = await list_shared_property_type_usage(pool, tenant_id, api_name)
    detached: list[str] = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            for entry in usage:
                ot_name = entry["object_type"]
                await _apply_detach_to_object_type(
                    conn, tenant_id=tenant_id, object_type_name=ot_name, api_name=api_name, spt=current
                )
                detached.append(ot_name)
            await conn.execute(
                "DELETE FROM shared_property_type WHERE tenant_id = $1 AND api_name = $2",
                tenant_id,
                api_name,
            )
    return {"api_name": api_name, "urn": current["urn"], "detached_object_types": detached}
