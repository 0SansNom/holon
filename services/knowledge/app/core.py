"""Shared read/authorization core, extracted out of `main.py`.

Every router module (`routers/*.py`) imports from here rather than from
`main.py` — this is the one piece every route group needs regardless of
domain: `_authorize_object_type` (28 call sites across every route group
that touches an ObjectType), `_resolve_one`/`_resolve_many` (the single
read choke point every object-read/action/export endpoint goes through),
and the masking/derived-property machinery those two call internally.

State (`pool`/`authz`/`allowed_countries`/`producer`) is a plain module
singleton, not threaded through `Request` — this service runs as one
process with one `lifespan()` (tests are black-box HTTP only, never
multiple in-process app instances), so there is nothing a `Request`-based
approach would guard against that a module-level assignment in
`lifespan()` doesn't already handle just as safely, at a fraction of the
diff. `main.py`'s `lifespan()` sets these once at startup, mirroring the
same values it already assigns to `app.state.*` for its own internal use
(background-task wiring, etc.).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Optional

from fastapi import HTTPException

from holon_common import Principal, build_urn, make_principal_dependency

from . import function_registry, ontology, resolver, serving_store
from pyiceberg.exceptions import NoSuchTableError

logger = logging.getLogger("knowledge")

TENANT_ID = os.environ["HOLON_TENANT_ID"]
WORKSPACE_ID = os.environ["HOLON_WORKSPACE_ID"]
JWT_SECRET = os.environ["HOLON_JWT_SECRET"]

ICEBERG_CONFIG = dict(
    catalog_uri=os.environ["HOLON_ICEBERG_CATALOG_URI"],
    warehouse=os.environ["HOLON_ICEBERG_WAREHOUSE"],
    s3_endpoint=os.environ["HOLON_S3_ENDPOINT"],
    access_key=os.environ["AWS_ACCESS_KEY_ID"],
    secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    region=os.environ["AWS_REGION"],
)

CUSTOMER_OBJECT_TYPE_URN = ontology.customer_object_type_urn(TENANT_ID, WORKSPACE_ID)
ORDER_OBJECT_TYPE_URN = ontology.order_object_type_urn(TENANT_ID, WORKSPACE_ID)
SUPPORT_TICKET_OBJECT_TYPE_URN = ontology.support_ticket_object_type_urn(TENANT_ID, WORKSPACE_ID)
PRODUCT_REVIEW_OBJECT_TYPE_URN = ontology.product_review_object_type_urn(TENANT_ID, WORKSPACE_ID)
SUPPLIER_OBJECT_TYPE_URN = ontology.supplier_object_type_urn(TENANT_ID, WORKSPACE_ID)
INVENTORY_LEVEL_OBJECT_TYPE_URN = ontology.inventory_level_object_type_urn(TENANT_ID, WORKSPACE_ID)
OBJECT_TYPE_URNS = {
    "Customer": CUSTOMER_OBJECT_TYPE_URN,
    "Order": ORDER_OBJECT_TYPE_URN,
    "SupportTicket": SUPPORT_TICKET_OBJECT_TYPE_URN,
    "ProductReview": PRODUCT_REVIEW_OBJECT_TYPE_URN,
    "Supplier": SUPPLIER_OBJECT_TYPE_URN,
    "InventoryLevel": INVENTORY_LEVEL_OBJECT_TYPE_URN,
}

# The export endpoint needs the same fetch_fn every existing
# list endpoint already uses — one small mapping, not a new read path.
FETCH_FNS = {
    "Customer": resolver.fetch_customers,
    "Order": resolver.fetch_orders,
    "SupportTicket": resolver.fetch_support_tickets,
    "ProductReview": resolver.fetch_reviews,
    "Supplier": resolver.fetch_suppliers,
    "InventoryLevel": resolver.fetch_inventory_levels,
}

# The instance-graph endpoint (routers/objects.py) needs the same
# id_kwarg every existing typed get-by-id endpoint already hardcodes —
# one small mapping, not a new derivation mechanism.
ID_KWARGS = {
    "Customer": "customer_id",
    "Order": "order_id",
    "SupportTicket": "ticket_id",
    "ProductReview": "review_id",
    "Supplier": "supplier_id",
    "InventoryLevel": "sku",
}

current_principal = make_principal_dependency(JWT_SECRET)

# Set once by `main.py`'s `lifespan()` at startup — see module docstring.
pool = None
authz = None


async def _object_type_urn_for(object_type: str) -> str:
    """`OBJECT_TYPE_URNS` first — a plain dict lookup, no DB round-trip,
    for the six boot-seeded types every hot path already expects to be
    fast. Falls back to a real query only for a self-serve type created
    at runtime (`ontology.create_object_type`), which was never known at
    import time and so can never be in that static dict. Kept as a
    fallback *inside* `_resolve_one`/`_resolve_many` rather than making
    `OBJECT_TYPE_URNS` itself dynamic (e.g. DB-refreshed on a timer),
    which would add real cache-staleness risk for a lookup this cheap to
    just do directly. Raises `KeyError` for a name that's neither —
    same contract the old bare `OBJECT_TYPE_URNS[object_type]` already
    had, so every existing call site's error behavior is unchanged.
    """
    static = OBJECT_TYPE_URNS.get(object_type)
    if static is not None:
        return static
    urn = ontology.object_type_urn(TENANT_ID, WORKSPACE_ID, object_type)
    row = await ontology.get_object_type(pool, urn)
    if row is None:
        raise KeyError(object_type)
    return urn


allowed_countries: set = set()
producer = None


async def _authorize_object_type(principal: Principal, object_type_urn: str, permission: str) -> None:
    """Shared by every object-type endpoint, read or write, Customer or
    Order (the PDP doesn't care which resource or verb it's checking —
    only `object_type_urn` and `permission` change).

    Behavior for `permission == "read"`: classification used to be passed
    straight through as the resource attribute, so OPA's
    `allow := false if classification == confidential and country not
    allowed` denied the *entire* object type wholesale for a
    disallowed-country principal — even its non-confidential properties.
    That's now handled at the correct granularity: `_mask_confidential_properties`
    masks only the actually-confidential fields, applied uniformly at the
    read choke point (`_resolve_one`/`_resolve_many`).
    ReBAC (can this principal read objects of this type at all) is unaffected —
    only the object-level ABAC classification gate is skipped for reads,
    since classification enforcement for reads now lives at the property
    level. Writes/approvals (`putOnCreditHold`, `closeAccount`, approving
    them) keep the original all-or-nothing check: masking has no meaning
    for a mutation — you can't partially deny writing to a field the
    request never even names them individually.
    """
    object_type = await ontology.get_object_type(pool, object_type_urn)
    if object_type is None:
        # Seeded at startup (ensure_seeded) — reaching here means that failed, not that
        # the resource is merely undefined. Fail loudly rather than guess a classification.
        raise HTTPException(status_code=500, detail=f"ObjectType {object_type_urn} is not catalogued")

    resource_attributes = {} if permission == "read" else {"classification": object_type["classification"]}
    decision = await authz.authorize(
        principal,
        resource_type="object_type",
        resource_urn=object_type_urn,
        permission=permission,
        resource_attributes=resource_attributes,
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)

    markings = object_type.get("markings") or []
    if markings and not await _authorize_markings(principal, markings):
        raise HTTPException(
            status_code=403, detail=f"missing required marking(s) on {object_type_urn}: {markings}"
        )


async def _authorize_markings(principal: Principal, markings: list[str]) -> bool:
    """Markings: composable, on top of ReBAC/ABAC, never
    instead of it — every listed marking must be held, checked directly
    against the flat `marking` SpiceDB resource (`hold = holder`, no
    cascading tree the way `object_type`'s own permissions have one) via
    `check_rebac`, the same primitive `authorize()` itself calls
    internally. Bypasses `authorize()`'s decision cache on purpose: that
    cache is keyed for the object_type/permission shape, not a variable-
    length marking list, and a marking check is a cheap flat SpiceDB
    lookup, not worth a second cache dimension for this build's scale.
    """
    for name in markings:
        marking_urn = build_urn(principal.tenant_id, "global", "marking", name)
        if not await authz.check_rebac(principal.urn, "marking", marking_urn, "hold"):
            return False
    return True


async def _mask_confidential_properties(
    object_type_urn: str, principal: Principal, rows: list[dict], *, key_prefix: str = ""
) -> list[dict]:
    """Row/column security enforcement point. A confidential
    property is replaced with `None` (and named in `_maskedFields`) rather
    than the whole object being withheld; a principal whose country
    passes ABAC gets every field, unmasked. Uses the same OPA-sourced
    `allowed_countries` set `search.py` mirrors (fetched once at startup
    via `PermissionClient.get_policy_data`) — consistent single source of
    truth, not a second hand-copied policy.

    `key_prefix`: `execute_plan`'s `join` operation returns rows
    with every column prefixed `s_`/`t_` to keep same-named columns from
    two different ObjectTypes from colliding — this masks
    `{key_prefix}{confidential_column}` instead of the bare column name,
    called once per side with each side's own classifications, same
    function either way.
    """
    if principal.country in allowed_countries:
        return rows
    property_classifications = await ontology.get_property_classifications(pool, object_type_urn)
    confidential_properties = {name for name, classification in property_classifications.items() if classification == "confidential"}
    if not confidential_properties:
        return rows

    masked_rows = []
    for row in rows:
        row = dict(row)
        masked_fields = [
            f"{key_prefix}{name}" for name in confidential_properties if row.get(f"{key_prefix}{name}") is not None
        ]
        for name in masked_fields:
            row[name] = None
        if masked_fields:
            row.setdefault("_maskedFields", [])
            row["_maskedFields"] = row["_maskedFields"] + masked_fields
        masked_rows.append(row)
    return masked_rows


async def _apply_derived_properties(object_type_urn: str, rows: list[dict]) -> list[dict]:
    """Read-time Function invocation. Runs on already-masked
    rows, translated to *ontology* property names via `property_mapping`
    (not the raw source-column keys `resolver.py`/`serving_store.py`
    return — a Function is an ontology-level concept, it shouldn't need
    to know storage column names). If a required input was masked to
    `None` by `_mask_confidential_properties`, the derived
    property is skipped entirely rather than computed from a missing
    value — never a misleading default silently leaking a shape of the
    masked data. Plugin lookups happen once per declared derived
    property, not once per row.
    """
    if not rows:
        return rows
    object_type = await ontology.get_object_type(pool, object_type_urn)
    derived = (object_type.get("derived_properties") or {}) if object_type else {}
    if not derived:
        return rows
    property_mapping = object_type["property_mapping"]

    resolved: dict[str, tuple[dict, Any]] = {}
    for property_name, function_name in derived.items():
        registration = await function_registry.find_active_function_by_name(pool, function_name)
        if registration is not None:
            resolved[property_name] = (registration, function_registry.load_function_plugin(registration["manifest"]))

    result_rows = []
    for row in rows:
        row = dict(row)
        translated = {camel: row.get(source_col) for camel, source_col in property_mapping.items()}
        for property_name, (registration, plugin) in resolved.items():
            required = (registration["manifest"].get("input_schema") or {}).get("required", [])
            if any(translated.get(field) is None for field in required):
                continue
            try:
                output = await plugin.call(**translated)
            except Exception:
                # Function plugins performing external I/O (such as HTTP calls to
                # Intelligence) may fail due to downstream outages or model errors.
                # Isolate plugin execution errors so a single failed derived property
                # leaves that property absent rather than failing the entire read.
                logger.exception(
                    "derived property %r (function %r) failed for %s, skipping it for this row",
                    property_name, registration["manifest"].get("function_name"), object_type_urn,
                )
                continue
            if isinstance(output, dict) and property_name in output:
                row[property_name] = output[property_name]
        result_rows.append(row)
    return result_rows


def _parse_struct_or_array(property_name: str, rule: dict, raw_value: Any) -> Any:
    """A `struct`/`array`-typed property's source column holds JSON
    text (the shape the no-code REST connector naturally leaves nested
    data in) — parsed here into a real nested dict/list, not left as an
    opaque string. `None` (missing, or already masked to `None` by
    `_mask_confidential_properties`) passes through unchanged — nothing
    to parse either way. A value that fails to parse or doesn't match
    the declared shape (dict for `struct`, list for `array`) is left as
    the raw string rather than raising — a read-path failure here would
    take down the whole row over one malformed property, the same
    "degrade, don't crash" treatment a failed derived-property Function
    already gets.
    """
    if raw_value is None or not isinstance(raw_value, str):
        return raw_value
    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError):
        return raw_value
    kind = rule.get("kind")
    if kind == "struct" and isinstance(parsed, dict):
        return parsed
    if kind == "array" and isinstance(parsed, list):
        return parsed
    return raw_value


async def _coerce_property_types(object_type_urn: str, rows: list[dict]) -> list[dict]:
    """Read-time struct/array parsing for `property_types` (see
    `ontology/object_types.py`'s DDL comment) — operates on the row's
    *raw* source-column keys, the same keys `_resolve_one`/`_resolve_many`
    already return unchanged for every ordinary property (property_mapping
    is a declared/checked contract, not a runtime rename — see
    `_apply_derived_properties`'s own docstring for why that translation
    only ever happens internally, just for a Function's inputs).
    """
    if not rows:
        return rows
    object_type = await ontology.get_object_type(pool, object_type_urn)
    property_types = (object_type.get("property_types") or {}) if object_type else {}
    structured = {name: rule for name, rule in property_types.items() if rule.get("kind") in ("struct", "array")}
    if not structured:
        return rows
    property_mapping = object_type["property_mapping"]

    result_rows = []
    for row in rows:
        row = dict(row)
        for property_name, rule in structured.items():
            source_col = property_mapping.get(property_name)
            if source_col is None or source_col not in row:
                continue
            row[source_col] = _parse_struct_or_array(property_name, rule, row[source_col])
        result_rows.append(row)
    return result_rows


async def _mask_and_derive(object_type_urn: str, principal: Principal, rows: list[dict]) -> list[dict]:
    """The combined read choke point: property masking, then struct/array
    type coercion, then derived properties — masking first since a
    derived property must never see a value the principal itself
    couldn't see unmasked; coercion before derivation so a Function
    declared against a structured property receives the real parsed
    shape, not a raw JSON string.
    """
    masked = await _mask_confidential_properties(object_type_urn, principal, rows)
    coerced = await _coerce_property_types(object_type_urn, masked)
    return await _apply_derived_properties(object_type_urn, coerced)


async def _filter_by_instance_markings(
    object_type_urn: str, tenant_id: str, principal: Principal, rows: list[dict]
) -> list[dict]:
    """Instance-level markings — the other attachment point
    alongside ObjectType-wide `markings` (already enforced earlier, in
    `_authorize_object_type`, before any row is even fetched). A row
    carrying an instance marking the principal doesn't hold is dropped
    entirely rather than masked — a marking is a coarse clearance gate,
    not a per-property one, so "denied" means the row doesn't exist for
    this principal, the same treatment ReBAC-denied resources already
    get elsewhere in this build (a filtered-out row surfaces as an empty
    list entry or, via `_resolve_one`, a 404 — never a 403 buried inside
    a 200 list).
    """
    if not rows:
        return rows
    instance_ids = [str(row["id"]) for row in rows]
    markings_by_instance = await ontology.get_instance_markings_bulk(
        pool, object_type_urn=object_type_urn, tenant_id=tenant_id, instance_ids=instance_ids
    )
    if not markings_by_instance:
        return rows
    result = []
    for row in rows:
        instance_markings = markings_by_instance.get(str(row["id"]))
        if instance_markings and not await _authorize_markings(principal, instance_markings):
            continue
        result.append(row)
    return result


async def _resolve_one(
    object_type: str,
    tenant_id: str,
    instance_id,
    fetch_fn,
    id_kwarg: str,
    *,
    principal: Principal,
    as_of: Optional[datetime] = None,
) -> Optional[dict]:
    """Serving-store read with a federated fallback: a miss
    here means nothing has been materialized for this key yet, not that
    the object doesn't exist — so degrade to a live scan via `resolver.py`
    rather than a false 404 or a 500.

    `as_of` historical read takes a different path entirely:
    a historical read either has recorded history to answer from or it
    doesn't, so it bypasses *both* the live serving-store read and the
    federated fallback above — degrading to "current" would silently answer
    a different question than the one asked.

    Property masking (and, after it, derived-property computation) is
    applied here unconditionally — this function (and `_resolve_many`
    below) is the single read choke point, so every one of the
    dozen-plus object-read endpoints gets it without a per-endpoint call.
    """
    object_type_urn = await _object_type_urn_for(object_type)

    if as_of is not None:
        row = await serving_store.get_instance_as_of(pool, object_type, tenant_id, instance_id, as_of)
        if row is None:
            return None
        filtered = await _filter_by_instance_markings(object_type_urn, tenant_id, principal, [row])
        if not filtered:
            return None
        return (await _mask_and_derive(object_type_urn, principal, filtered))[0]

    data = await serving_store.get_instance(pool, object_type, tenant_id, instance_id)
    if data is not None:
        filtered = await _filter_by_instance_markings(object_type_urn, tenant_id, principal, [data])
        if not filtered:
            return None
        return (await _mask_and_derive(object_type_urn, principal, filtered))[0]
    try:
        rows = await asyncio.to_thread(fetch_fn, **{id_kwarg: instance_id}, **ICEBERG_CONFIG)
    except NoSuchTableError:
        # No sync has ever run for this ObjectType — genuinely zero
        # instances exist yet, not a server error. Same "degrade instead
        # of 500" contract this fallback already promises for an
        # ordinary serving-store miss.
        return None
    if not rows:
        return None
    row = dict(rows[0])
    row["materializedAt"] = None
    row["sourceLagSeconds"] = 0
    row["degraded"] = True
    filtered = await _filter_by_instance_markings(object_type_urn, tenant_id, principal, [row])
    if not filtered:
        return None
    return (await _mask_and_derive(object_type_urn, principal, filtered))[0]


async def _resolve_many(
    object_type: str,
    tenant_id: str,
    fetch_fn,
    *,
    principal: Principal,
    filter_column: Optional[str] = None,
    filter_kwarg: Optional[str] = None,
    filter_value=None,
) -> list[dict]:
    object_type_urn = await _object_type_urn_for(object_type)
    rows = await serving_store.list_instances(
        pool, object_type, tenant_id, filter_column=filter_column, filter_value=filter_value
    )
    if rows:
        rows = await _filter_by_instance_markings(object_type_urn, tenant_id, principal, rows)
        return await _mask_and_derive(object_type_urn, principal, rows)
    kwargs = {filter_kwarg: filter_value} if filter_kwarg else {}
    try:
        live_rows = await asyncio.to_thread(fetch_fn, **kwargs, **ICEBERG_CONFIG)
    except NoSuchTableError:
        # No sync has ever run for this ObjectType — genuinely zero
        # instances exist yet, not a server error. Same "degrade instead
        # of 500" contract this fallback already promises for an
        # ordinary serving-store miss.
        live_rows = []
    result = []
    for row in live_rows:
        row = dict(row)
        row["materializedAt"] = None
        row["sourceLagSeconds"] = 0
        row["degraded"] = True
        result.append(row)
    result = await _filter_by_instance_markings(object_type_urn, tenant_id, principal, result)
    return await _mask_and_derive(object_type_urn, principal, result)
