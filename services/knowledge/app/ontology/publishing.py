"""ObjectType lifecycle: propose a draft, publish it. The widest-reaching
module in this package — publishing touches interfaces, derived-property
Function registration, project scope, and markings validation all in one
transaction — kept intact rather than fragmented further, per the plan's
own note on this function.
"""

from __future__ import annotations

import json
import uuid
from typing import Optional

import asyncpg
import httpx

from holon_common import EventActor, EventEnvelope, build_urn, outbox

from . import markings as markings_module
from .object_types import get_object_type, get_object_type_version
from .interfaces import get_interface_type


async def _validate_implements(
    pool: asyncpg.Pool, *, tenant_id: str, object_type_name: str, property_mapping: dict, implements: list[str]
) -> None:
    """Enforced at publish time (same synchronous-validation treatment
    `create_relation_type` already gives cardinality/endpoints):
    declaring conformance to an interface is a checked promise, not a
    label. Every required property must exist on this version's own
    `property_mapping`; every required action must be a real, registered
    Action targeting this ObjectType.
    """
    from ..actions import ACTION_DEFINITIONS

    for interface_name in implements:
        interface = await get_interface_type(pool, tenant_id, interface_name)
        if interface is None:
            raise ValueError(f"unknown interface: {interface_name!r}")
        missing_properties = [p for p in interface["required_properties"] if p not in property_mapping]
        if missing_properties:
            raise ValueError(
                f"{object_type_name} cannot implement {interface_name!r}: "
                f"missing required propert{'y' if len(missing_properties) == 1 else 'ies'} {missing_properties}"
            )
        object_action_names = {
            action_name.split(".", 1)[1]
            for action_name, definition in ACTION_DEFINITIONS.items()
            if definition["target_object_type"] == object_type_name
        }
        missing_actions = [a for a in interface["required_actions"] if a not in object_action_names]
        if missing_actions:
            raise ValueError(
                f"{object_type_name} cannot implement {interface_name!r}: "
                f"missing required action{'s' if len(missing_actions) != 1 else ''} {missing_actions}"
            )


async def _validate_derived_properties(pool: asyncpg.Pool, *, derived_properties: dict[str, str]) -> None:
    """Enforced at publish time, same tier as `_validate_implements`:
    a derived property must name a real, currently-*active* Function
    plugin — not just any string. Import kept local to avoid a
    module-load-order assumption between this package and
    `function_registry.py` (neither currently imports the other at
    top level; this keeps it that way).
    """
    from .. import function_registry

    for property_name, function_name in derived_properties.items():
        plugin = await function_registry.find_active_function_by_name(pool, function_name)
        if plugin is None:
            raise ValueError(
                f"derived property {property_name!r} names {function_name!r}, "
                f"which is not a registered, active Function plugin"
            )


_ALLOWED_FORMAT_KINDS = {"currency", "badge"}
# The same small, closed vocabulary Blueprint's own `Intent` type uses
# (`Tag`/`Callout` `intent` prop) — a `badge` rule's colors map values
# directly onto it so the frontend never needs its own color table.
_ALLOWED_BADGE_COLORS = {"primary", "success", "warning", "danger", "none"}


async def _validate_property_formats(
    *, property_mapping: dict, derived_properties: dict[str, str], property_formats: dict[str, dict]
) -> None:
    """Enforced at publish time, same tier as `_validate_derived_properties`:
    a format rule must name a real property (mapped or derived) and a
    known `kind` with a well-formed rule body — not just any JSON blob a
    caller happened to send.
    """
    known_properties = set(property_mapping) | set(derived_properties)
    for property_name, rule in property_formats.items():
        if property_name not in known_properties:
            raise ValueError(f"format rule for {property_name!r} names a property this ObjectType doesn't have")
        kind = rule.get("kind")
        if kind not in _ALLOWED_FORMAT_KINDS:
            raise ValueError(f"format rule for {property_name!r} has unknown kind {kind!r} (expected one of {sorted(_ALLOWED_FORMAT_KINDS)})")
        if kind == "currency":
            if not isinstance(rule.get("currency"), str) or len(rule["currency"]) != 3:
                raise ValueError(f"format rule for {property_name!r}: 'currency' must be a 3-letter code (e.g. 'USD')")
        elif kind == "badge":
            colors = rule.get("colors")
            if not isinstance(colors, dict) or not colors:
                raise ValueError(f"format rule for {property_name!r}: 'colors' must be a non-empty value->color mapping")
            bad_colors = {c for c in colors.values() if c not in _ALLOWED_BADGE_COLORS}
            if bad_colors:
                raise ValueError(f"format rule for {property_name!r}: unknown badge color(s) {bad_colors} (expected one of {sorted(_ALLOWED_BADGE_COLORS)})")


_ALLOWED_PROPERTY_TYPE_KINDS = {"value_type", "shared_property_type", "struct", "array"}


async def _validate_property_types(
    pool: asyncpg.Pool, *, tenant_id: str, property_mapping: dict, derived_properties: dict[str, str], property_types: dict[str, dict]
) -> None:
    """Enforced at publish time, same tier as `_validate_property_formats`
    (a genuinely separate concern — data typing, not display formatting):
    a property_types entry must name a real property, a known `kind`,
    and — for `value_type`/`shared_property_type` — a real, registered
    reference. `struct`/`array` nesting is checked structurally but
    limited to one level (their own `properties`/`element` entries may
    only be `value_type`/`shared_property_type`, never another `struct`/
    `array`) — an explicit, stated scope boundary, not a silently-
    enforced accidental one.
    """
    from . import shared_property_types as shared_property_types_module
    from . import value_types as value_types_module

    known_properties = set(property_mapping) | set(derived_properties)

    async def _validate_leaf(property_name: str, rule: dict, *, nested: bool) -> None:
        kind = rule.get("kind")
        if kind not in _ALLOWED_PROPERTY_TYPE_KINDS:
            raise ValueError(f"property_types entry for {property_name!r} has unknown kind {kind!r} (expected one of {sorted(_ALLOWED_PROPERTY_TYPE_KINDS)})")
        if kind == "value_type":
            value_type_name = rule.get("value_type")
            if await value_types_module.get_value_type(pool, tenant_id, value_type_name) is None:
                raise ValueError(f"property_types entry for {property_name!r} names unknown value_type {value_type_name!r}")
            return
        if kind == "shared_property_type":
            shared_property_type_name = rule.get("shared_property_type")
            if await shared_property_types_module.get_shared_property_type(pool, tenant_id, shared_property_type_name) is None:
                raise ValueError(f"property_types entry for {property_name!r} names unknown shared_property_type {shared_property_type_name!r}")
            return
        if nested:
            raise ValueError(f"property_types entry for {property_name!r}: struct/array nesting is limited to one level")
        if kind == "struct":
            nested_properties = rule.get("properties")
            if not isinstance(nested_properties, dict) or not nested_properties:
                raise ValueError(f"property_types entry for {property_name!r}: 'struct' requires a non-empty 'properties' dict")
            for nested_name, nested_rule in nested_properties.items():
                await _validate_leaf(f"{property_name}.{nested_name}", nested_rule, nested=True)
        elif kind == "array":
            element_rule = rule.get("element")
            if not isinstance(element_rule, dict):
                raise ValueError(f"property_types entry for {property_name!r}: 'array' requires an 'element' type rule")
            await _validate_leaf(f"{property_name}[]", element_rule, nested=True)

    for property_name, rule in property_types.items():
        if property_name not in known_properties:
            raise ValueError(f"property_types entry for {property_name!r} names a property this ObjectType doesn't have")
        await _validate_leaf(property_name, rule, nested=False)


async def _validate_project_scope(*, identity_url: str, project_urn: str, identity_token: str) -> None:
    """Enforced at publish time, same tier as the other validations.
    Knowledge never reads Identity's database directly — same
    cross-service boundary this build keeps everywhere else (Qdrant
    indexing calls Knowledge's HTTP API rather than its Postgres, the
    exact same reasoning applies here in reverse).
    """
    local_name = project_urn.rsplit(":", 1)[-1]
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{identity_url}/projects/{local_name}", headers={"Authorization": f"Bearer {identity_token}"}
        )
    if response.status_code == 404:
        raise ValueError(f"unknown project: {project_urn!r}")
    response.raise_for_status()


async def propose_object_type_version(
    pool: asyncpg.Pool,
    *,
    object_type_urn: str,
    property_mapping: Optional[dict] = None,
    description: Optional[str] = None,
    implements: Optional[list[str]] = None,
    derived_properties: Optional[dict[str, str]] = None,
    project_urn: Optional[str] = None,
    markings: Optional[list[str]] = None,
    property_formats: Optional[dict[str, dict]] = None,
    property_types: Optional[dict[str, dict]] = None,
) -> dict:
    """Creates a `draft` version — never touches the live `object_type`
    row (everything else in this build keeps reading the current
    *published* state until `publish_object_type_version` says
    otherwise). A partial update (only `description`, say) carries the
    current published value forward for whatever isn't overridden, so
    proposing a version never silently blanks out the other field.
    """
    current = await get_object_type(pool, object_type_urn)
    if current is None:
        raise ValueError(f"unknown ObjectType: {object_type_urn}")

    # Drafts that fail publishing validation remain in `object_type_version`
    # as unpublished drafts. Compute the next version number relative to the
    # maximum version in `object_type_version` to prevent UNIQUE constraint collisions.
    highest_known_version = await pool.fetchval(
        "SELECT COALESCE(MAX(version), 0) FROM object_type_version WHERE object_type_urn = $1", object_type_urn
    )
    next_version = max(current["version"], highest_known_version) + 1
    new_mapping = property_mapping if property_mapping is not None else current["property_mapping"]
    if isinstance(new_mapping, str):
        new_mapping = json.loads(new_mapping)
    new_description = description if description is not None else current["description"]
    new_implements = implements if implements is not None else (current.get("implements") or [])
    new_derived_properties = (
        derived_properties if derived_properties is not None else (current.get("derived_properties") or {})
    )
    new_project_urn = project_urn if project_urn is not None else current.get("project_urn")
    new_markings = markings if markings is not None else (current.get("markings") or [])
    new_property_formats = (
        property_formats if property_formats is not None else (current.get("property_formats") or {})
    )
    new_property_types = (
        property_types if property_types is not None else (current.get("property_types") or {})
    )

    await pool.execute(
        """
        INSERT INTO object_type_version
            (object_type_urn, tenant_id, version, property_mapping, description, implements, derived_properties,
             project_urn, markings, property_formats, property_types, status)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6::jsonb, $7::jsonb, $8, $9::jsonb, $10::jsonb, $11::jsonb, 'draft')
        """,
        object_type_urn, current["tenant_id"], next_version,
        json.dumps(new_mapping), new_description, json.dumps(new_implements), json.dumps(new_derived_properties),
        new_project_urn, json.dumps(new_markings), json.dumps(new_property_formats), json.dumps(new_property_types),
    )
    return await get_object_type_version(pool, object_type_urn, next_version)


async def publish_object_type_version(
    pool: asyncpg.Pool,
    *,
    object_type_urn: str,
    version: int,
    identity_url: Optional[str] = None,
    identity_token: Optional[str] = None,
) -> dict:
    """The only thing that ever updates the live `object_type` row past
    its bootstrap state — every other reader in this build
    (`resolver.py`, `serving_store.py`, `search.py`, every `/objects/...`
    endpoint) keeps working unchanged, since they all read `object_type`
    as before. Publishes `knowledge.objecttype.published` (transactional outbox).

    `identity_url`/`identity_token` are only required when the draft
    actually declares a `project_urn` — kept optional rather than forcing
    every caller to thread through a dependency it doesn't need.
    """
    draft = await get_object_type_version(pool, object_type_urn, version)
    if draft is None:
        raise ValueError(f"no version {version} found for {object_type_urn}")
    if draft["status"] == "published":
        raise ValueError(f"version {version} of {object_type_urn} is already published")

    current = await get_object_type(pool, object_type_urn)
    previous_version = current["version"] if current else None
    implements = draft.get("implements") or []
    derived_properties = draft.get("derived_properties") or {}
    project_urn = draft.get("project_urn")
    markings = draft.get("markings") or []
    property_formats = draft.get("property_formats") or {}
    property_types = draft.get("property_types") or {}

    if implements:
        await _validate_implements(
            pool,
            tenant_id=draft["tenant_id"],
            object_type_name=current["name"] if current else object_type_urn.rsplit(":", 1)[-1],
            property_mapping=draft["property_mapping"],
            implements=implements,
        )
    if derived_properties:
        await _validate_derived_properties(pool, derived_properties=derived_properties)
    if project_urn:
        if identity_url is None or identity_token is None:
            raise ValueError("project_urn is set but no identity_url/identity_token was provided to validate it against")
        await _validate_project_scope(identity_url=identity_url, project_urn=project_urn, identity_token=identity_token)
    if markings:
        await markings_module._validate_markings(pool, tenant_id=draft["tenant_id"], markings=markings)
    if property_formats:
        await _validate_property_formats(
            property_mapping=draft["property_mapping"],
            derived_properties=derived_properties,
            property_formats=property_formats,
        )
    if property_types:
        await _validate_property_types(
            pool,
            tenant_id=draft["tenant_id"],
            property_mapping=draft["property_mapping"],
            derived_properties=derived_properties,
            property_types=property_types,
        )

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE object_type_version SET status = 'published', published_at = now() WHERE object_type_urn = $1 AND version = $2",
            object_type_urn, version,
        )
        await conn.execute(
            """
            UPDATE object_type SET version = $1, property_mapping = $2::jsonb, description = $3,
                implements = $4::jsonb, derived_properties = $5::jsonb, project_urn = $6, markings = $7::jsonb,
                property_formats = $8::jsonb, property_types = $9::jsonb
            WHERE urn = $10
            """,
            version, json.dumps(draft["property_mapping"]), draft["description"],
            json.dumps(implements), json.dumps(derived_properties), project_urn, json.dumps(markings),
            json.dumps(property_formats), json.dumps(property_types), object_type_urn,
        )
        event_id = uuid.uuid4().hex
        event = EventEnvelope(
            event_id=event_id,
            event_type="knowledge.objecttype.published",
            tenant_id=draft["tenant_id"],
            aggregate_type="ObjectType",
            aggregate_id=object_type_urn,
            correlation_id=event_id,
            partition_key=f"{draft['tenant_id']}/{object_type_urn}",
            producer="knowledge-platform@0.1.0",
            actor=EventActor(type="service_account", urn=build_urn(draft["tenant_id"], "global", "service-account", "ontology-governance")),
            payload={
                "object_type_urn": object_type_urn,
                "name": current["name"] if current else object_type_urn,
                "version": version,
                "previous_version": previous_version,
            },
        )
        await outbox.enqueue(conn, event)

    return await get_object_type(pool, object_type_urn)
