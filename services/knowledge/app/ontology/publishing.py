"""ObjectType lifecycle: propose and publish versioned definitions."""

from __future__ import annotations

import json
import re
import uuid
from typing import Optional

import asyncpg
import httpx

from holon_common import EventActor, EventEnvelope, build_urn, outbox
from holon_common.correlation import current_correlation_id

from . import markings as markings_module
from .object_types import get_object_type, get_object_type_version, validate_ot_metadata
from .type_classes import normalize_type_classes
from .render_hints import ALLOWED_RENDER_HINTS, normalize_render_hints


from .publishing_validate import (
    _validate_implements,
    assert_interface_tighten_compatible,  # re-exported; interfaces.py imports it from here
    _validate_derived_properties,
    _validate_property_formats,
    _validate_conditional_formats,
    _validate_property_types,
    _validate_project_scope,
)

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
    conditional_formats: Optional[dict[str, list]] = None,
    property_types: Optional[dict[str, dict]] = None,
    link_constraint_bindings: Optional[dict] = None,
    interface_property_bindings: Optional[dict] = None,
    primary_key: Optional[str] = None,
    title_key: Optional[str] = None,
    plural_display_name: Optional[str] = None,
    lifecycle_status: Optional[str] = None,
    visibility: Optional[str] = None,
    icon: Optional[str] = None,
    deprecation_reason: Optional[str] = None,
    deprecation_deadline=None,
    replacement_urn: Optional[str] = None,
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
    new_conditional_formats = (
        conditional_formats if conditional_formats is not None else (current.get("conditional_formats") or {})
    )
    new_property_types = (
        property_types if property_types is not None else (current.get("property_types") or {})
    )
    new_link_bindings = (
        link_constraint_bindings
        if link_constraint_bindings is not None
        else (current.get("link_constraint_bindings") or {})
    )
    new_iface_prop_bindings = (
        interface_property_bindings
        if interface_property_bindings is not None
        else (current.get("interface_property_bindings") or {})
    )
    new_primary_key = primary_key if primary_key is not None else (current.get("primary_key") or "id")
    new_title_key = title_key if title_key is not None else current.get("title_key")
    new_plural = plural_display_name if plural_display_name is not None else (current.get("plural_display_name") or "")
    new_lifecycle = lifecycle_status if lifecycle_status is not None else (current.get("lifecycle_status") or "experimental")
    new_visibility = visibility if visibility is not None else (current.get("visibility") or "normal")
    new_icon = icon if icon is not None else current.get("icon")
    # Deprecation fields: explicit override when provided; else carry forward
    # from live (normalize clears them when status ≠ deprecated).
    new_dep_reason = (
        deprecation_reason if deprecation_reason is not None else current.get("deprecation_reason")
    )
    new_dep_deadline = (
        deprecation_deadline if deprecation_deadline is not None else current.get("deprecation_deadline")
    )
    new_replacement = (
        replacement_urn if replacement_urn is not None else current.get("replacement_urn")
    )

    dep = validate_ot_metadata(
        property_mapping=new_mapping,
        primary_key=new_primary_key,
        title_key=new_title_key,
        lifecycle_status=new_lifecycle,
        visibility=new_visibility,
        deprecation_reason=new_dep_reason,
        deprecation_deadline=new_dep_deadline,
        replacement_urn=new_replacement,
    )

    await pool.execute(
        """
        INSERT INTO object_type_version
            (object_type_urn, tenant_id, version, property_mapping, description, implements, derived_properties,
             project_urn, markings, property_formats, conditional_formats, property_types,
             link_constraint_bindings, interface_property_bindings,
             primary_key, title_key, plural_display_name, lifecycle_status, visibility, icon,
             deprecation_reason, deprecation_deadline, replacement_urn, status)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6::jsonb, $7::jsonb, $8, $9::jsonb, $10::jsonb, $11::jsonb, $12::jsonb,
                $13::jsonb, $14::jsonb,
                $15, $16, $17, $18, $19, $20, $21, $22, $23, 'draft')
        """,
        object_type_urn, current["tenant_id"], next_version,
        json.dumps(new_mapping), new_description, json.dumps(new_implements), json.dumps(new_derived_properties),
        new_project_urn, json.dumps(new_markings), json.dumps(new_property_formats),
        json.dumps(new_conditional_formats), json.dumps(new_property_types),
        json.dumps(new_link_bindings), json.dumps(new_iface_prop_bindings),
        new_primary_key, new_title_key, new_plural, dep["lifecycle_status"], new_visibility, new_icon,
        dep["deprecation_reason"], dep["deprecation_deadline"], dep["replacement_urn"],
    )
    return await get_object_type_version(pool, object_type_urn, next_version)


async def _run_publish_validations(
    pool: asyncpg.Pool,
    *,
    draft: dict,
    current: dict | None,
    object_type_name: str,
    implements: list,
    derived_properties: dict,
    project_urn: Optional[str],
    markings: list,
    property_formats: dict,
    conditional_formats: dict,
    property_types: dict,
    link_constraint_bindings: Optional[dict] = None,
    interface_property_bindings: Optional[dict] = None,
    identity_url: Optional[str] = None,
    identity_token: Optional[str] = None,
) -> None:
    """All publish-time validations, extracted so they run *outside* a
    database transaction. Some validations make HTTP calls (`_validate_project_scope`
    via httpx) — holding a DB connection open for the duration of a remote
    request would waste a pool slot unnecessarily. The two sync validators
    (`_validate_property_formats`, `_validate_conditional_formats`) are
    also called here without `await`.
    """
    primary_key = draft.get("primary_key") or "id"
    title_key = draft.get("title_key")
    lifecycle_status = draft.get("lifecycle_status") or "experimental"
    visibility = draft.get("visibility") or "normal"
    validate_ot_metadata(
        property_mapping=draft["property_mapping"],
        primary_key=primary_key,
        title_key=title_key,
        lifecycle_status=lifecycle_status,
        visibility=visibility,
        deprecation_reason=draft.get("deprecation_reason"),
        deprecation_deadline=draft.get("deprecation_deadline"),
        replacement_urn=draft.get("replacement_urn"),
    )
    if current and (current.get("lifecycle_status") or "experimental") in ("active", "promoted"):
        live_pk = current.get("primary_key") or "id"
        if primary_key != live_pk:
            raise ValueError(
                f"cannot change primary_key from {live_pk!r} to {primary_key!r} while "
                f"lifecycle_status is active or promoted — deprecate or keep the existing key"
            )
    if implements:
        await _validate_implements(
            pool,
            tenant_id=draft["tenant_id"],
            object_type_name=object_type_name,
            property_mapping=draft["property_mapping"],
            implements=implements,
            property_types=property_types,
            link_constraint_bindings=link_constraint_bindings or draft.get("link_constraint_bindings") or {},
            interface_property_bindings=(
                interface_property_bindings
                or draft.get("interface_property_bindings")
                or {}
            ),
        )
    if derived_properties:
        await _validate_derived_properties(
            pool,
            derived_properties=derived_properties,
            object_type_name=object_type_name,
            tenant_id=draft["tenant_id"],
            property_types=property_types,
        )
    if project_urn:
        if identity_url is None or identity_token is None:
            raise ValueError("project_urn is set but no identity_url/identity_token was provided to validate it against")
        await _validate_project_scope(identity_url=identity_url, project_urn=project_urn, identity_token=identity_token)
    if markings:
        await markings_module._validate_markings(pool, tenant_id=draft["tenant_id"], markings=markings)
    if property_formats:
        _validate_property_formats(
            property_mapping=draft["property_mapping"],
            derived_properties=derived_properties,
            property_formats=property_formats,
        )
    if conditional_formats:
        _validate_conditional_formats(
            property_mapping=draft["property_mapping"],
            derived_properties=derived_properties,
            conditional_formats=conditional_formats,
        )
    if property_types:
        await _validate_property_types(
            pool,
            tenant_id=draft["tenant_id"],
            property_mapping=draft["property_mapping"],
            derived_properties=derived_properties,
            property_types=property_types,
        )


async def _write_publish(
    conn: asyncpg.Connection,
    *,
    object_type_urn: str,
    version: int,
    draft: dict,
    current: dict | None,
    previous_version: Optional[int],
    implements: list,
    derived_properties: dict,
    project_urn: Optional[str],
    markings: list,
    property_formats: dict,
    conditional_formats: dict,
    property_types: dict,
) -> None:
    """Execute the publish writes inside an already-open transaction.

    Separated from validation so that callers can include additional writes
    (e.g. a branch-merge status update) in the same atomic transaction
    without duplicating the publish logic.

    Acquires a row-level lock (`FOR UPDATE`) on the `object_type` row first
    to serialise concurrent publishments on the same ObjectType — a second
    concurrent call will block here until this transaction commits or rolls
    back, preventing two concurrent callers from each validating against the
    same version and then both writing. Re-checks monotonicity under that
    lock so a stale draft (version ≤ live) cannot silently regress the
    published definition even if another publish raced ahead between the
    caller's pre-checks and this write.
    """
    locked = await conn.fetchrow("SELECT version FROM object_type WHERE urn = $1 FOR UPDATE", object_type_urn)
    live_version = locked["version"] if locked is not None else None
    if live_version is not None and version <= live_version:
        raise ValueError(
            f"cannot publish version {version} of {object_type_urn}: "
            f"live is already at version {live_version}"
        )
    # Prefer the locked read for the event payload — `previous_version`
    # passed by the caller may be stale after a concurrent publish.
    event_previous_version = live_version if live_version is not None else previous_version
    await conn.execute(
        "UPDATE object_type_version SET status = 'published', published_at = now() WHERE object_type_urn = $1 AND version = $2",
        object_type_urn, version,
    )
    lifecycle = draft.get("lifecycle_status") or "experimental"
    if lifecycle in ("experimental", "deprecated", "example") and isinstance(property_types, dict):
        cascaded_props: dict = {}
        for key, rule in property_types.items():
            if isinstance(rule, dict):
                cascaded_props[key] = {**rule, "lifecycle_status": lifecycle}
            else:
                cascaded_props[key] = rule
        property_types = cascaded_props

    await conn.execute(
        """
        UPDATE object_type SET version = $1, property_mapping = $2::jsonb, description = $3,
            implements = $4::jsonb, derived_properties = $5::jsonb, project_urn = $6, markings = $7::jsonb,
            property_formats = $8::jsonb, conditional_formats = $9::jsonb, property_types = $10::jsonb,
            link_constraint_bindings = $11::jsonb, interface_property_bindings = $12::jsonb,
            primary_key = $13, title_key = $14, plural_display_name = $15,
            lifecycle_status = $16, visibility = $17, icon = $18,
            deprecation_reason = $19, deprecation_deadline = $20, replacement_urn = $21
        WHERE urn = $22
        """,
        version, json.dumps(draft["property_mapping"]), draft["description"],
        json.dumps(implements), json.dumps(derived_properties), project_urn, json.dumps(markings),
        json.dumps(property_formats), json.dumps(conditional_formats), json.dumps(property_types),
        json.dumps(draft.get("link_constraint_bindings") or {}),
        json.dumps(draft.get("interface_property_bindings") or {}),
        draft.get("primary_key") or "id", draft.get("title_key"), draft.get("plural_display_name") or "",
        draft.get("lifecycle_status") or "experimental", draft.get("visibility") or "normal", draft.get("icon"),
        draft.get("deprecation_reason"), draft.get("deprecation_deadline"), draft.get("replacement_urn"),
        object_type_urn,
    )
    from .relation_types import cascade_lifecycle_from_object_type

    await cascade_lifecycle_from_object_type(
        conn,
        object_type_urn=object_type_urn,
        lifecycle_status=draft.get("lifecycle_status") or "experimental",
    )
    event_id = uuid.uuid4().hex
    event = EventEnvelope(
        event_id=event_id,
        event_type="knowledge.objecttype.published",
        tenant_id=draft["tenant_id"],
        aggregate_type="ObjectType",
        aggregate_id=object_type_urn,
        correlation_id=current_correlation_id() or event_id,
        partition_key=f"{draft['tenant_id']}/{object_type_urn}",
        producer="knowledge-platform@0.1.0",
        actor=EventActor(type="service_account", urn=build_urn(draft["tenant_id"], "global", "service-account", "ontology-governance")),
        payload={
            "object_type_urn": object_type_urn,
            "name": current["name"] if current else object_type_urn,
            "version": version,
            "previous_version": event_previous_version,
        },
    )
    await outbox.enqueue(conn, event)


def _invalidate_published_cache(object_type_urn: str, *, current: Optional[dict], draft: Optional[dict]) -> None:
    tenant_id = (current or {}).get("tenant_id") or (draft or {}).get("tenant_id")
    if not tenant_id and object_type_urn.startswith("hl:"):
        parts = object_type_urn.split(":")
        tenant_id = parts[1] if len(parts) > 1 else None
    from . import definition_cache

    definition_cache.invalidate_object_type(urn=object_type_urn, tenant_id=tenant_id)


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
    # Fast-fail before expensive validations (and again under FOR UPDATE
    # in `_write_publish`) so publishing an older draft cannot silently
    # regress the live definition.
    if previous_version is not None and version <= previous_version:
        raise ValueError(
            f"cannot publish version {version} of {object_type_urn}: "
            f"live is already at version {previous_version}"
        )
    object_type_name = current["name"] if current else object_type_urn.rsplit(":", 1)[-1]
    implements = draft.get("implements") or []
    derived_properties = draft.get("derived_properties") or {}
    project_urn = draft.get("project_urn")
    markings = draft.get("markings") or []
    property_formats = draft.get("property_formats") or {}
    conditional_formats = draft.get("conditional_formats") or {}
    property_types = draft.get("property_types") or {}
    link_constraint_bindings = draft.get("link_constraint_bindings") or {}
    interface_property_bindings = draft.get("interface_property_bindings") or {}

    await _run_publish_validations(
        pool,
        draft=draft,
        current=current,
        object_type_name=object_type_name,
        implements=implements,
        derived_properties=derived_properties,
        project_urn=project_urn,
        markings=markings,
        property_formats=property_formats,
        conditional_formats=conditional_formats,
        property_types=property_types,
        link_constraint_bindings=link_constraint_bindings,
        interface_property_bindings=interface_property_bindings,
        identity_url=identity_url,
        identity_token=identity_token,
    )

    async with pool.acquire() as conn, conn.transaction():
        await _write_publish(
            conn,
            object_type_urn=object_type_urn,
            version=version,
            draft=draft,
            current=current,
            previous_version=previous_version,
            implements=implements,
            derived_properties=derived_properties,
            project_urn=project_urn,
            markings=markings,
            property_formats=property_formats,
            conditional_formats=conditional_formats,
            property_types=property_types,
        )

    _invalidate_published_cache(object_type_urn, current=current, draft=draft)
    return await get_object_type(pool, object_type_urn)
