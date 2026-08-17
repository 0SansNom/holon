"""Generic self-serve reads — `ontology.create_object_type`'s counterpart.
Registered last, on purpose: `objects/__init__.py` combines this
router's routes after `object_reads.py`'s, per that file's own module
docstring (Starlette matches by registration order) — these two routes
are the most general shape in the package (a bare `/objects/{object_type}`
and `/objects/{object_type}/{instance_id}`), and would shadow every
specific route in `object_reads.py` if registered any earlier, including
`/objects/Customer` itself. They only ever get reached for a name that
isn't one of the six boot-known types, at which point
`core._resolve_one`/`_resolve_many`'s own dynamic-URN fallback and
`resolver.fetch_generic` take over. `_merge_declarative_edits` below
overlays Action Type edits for every ObjectType.
"""

from __future__ import annotations

import functools
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from holon_common import HolonError, Principal

from ... import ontology, resolver
from ... import core
from ...actions import ActionValidationError, request_generic_action, revert_declarative_action, validate_generic_action
from ...actions.declarative import _object_type_and_instance_id_from_instance_urn
from ...actions.wire import success_envelope, validation_report
from .paging_deps import after_id_from_cursor, page_from_resolved, paging_query

router = APIRouter()

logger = logging.getLogger("knowledge.objects")


async def _generic_object_type_or_404(object_type: str, principal: Principal) -> dict:
    types = await ontology.list_object_types(core.pool, principal.tenant_id)
    for definition in types:
        if definition.get("name") == object_type or definition["urn"].rsplit(":", 1)[-1] == object_type:
            return definition
    raise HolonError.not_found("ObjectTypeNotFound", f"unknown ObjectType: {object_type}", object_type=object_type)


async def _merge_declarative_edits(rows: list[dict], object_type: str, tenant_id: str) -> list[dict]:
    """The generic read half of the `actions` package's
    `object_instance_edit` overlay — a declarative Action Type's applied
    edits become visible on the very next read, for every ObjectType.
    """
    if not rows:
        return rows
    instance_ids = [str(row["id"]) for row in rows]
    edit_rows = await core.pool.fetch(
        "SELECT instance_id, property_name, property_value FROM object_instance_edit "
        "WHERE tenant_id = $1 AND object_type = $2 AND instance_id = ANY($3::text[])",
        tenant_id, object_type, instance_ids,
    )
    if not edit_rows:
        return rows
    edits_by_instance: dict[str, dict] = {}
    for edit in edit_rows:
        value = edit["property_value"]
        if isinstance(value, str):
            value = json.loads(value)
        edits_by_instance.setdefault(edit["instance_id"], {})[edit["property_name"]] = value
    result = []
    for row in rows:
        row = dict(row)
        row.update(edits_by_instance.get(str(row["id"]), {}))
        result.append(row)
    return result


@router.get("/objects/{object_type}")
async def list_generic_objects(
    object_type: str,
    principal: Principal = Depends(core.current_principal),
    page: tuple[int, Optional[str]] = Depends(paging_query),
) -> dict:
    page_size, cursor = page
    definition = await _generic_object_type_or_404(object_type, principal)
    await core._authorize_object_type(principal, definition["urn"], "read")
    dataset_name = definition["source_dataset_urn"].rsplit(":", 1)[-1]
    fetch_fn = functools.partial(resolver.fetch_generic, dataset_name)
    rows = await core._resolve_many(
        object_type, principal.tenant_id, fetch_fn, principal=principal,
        after_id=after_id_from_cursor(cursor), limit=page_size + 1,
    )
    rows = await _merge_declarative_edits(rows, object_type, principal.tenant_id)
    for row in rows:
        row["title"] = ontology.title_of(row, definition)
    from ...api.object_wire import enrich_page

    return enrich_page(
        page_from_resolved(rows, page_size=page_size),
        object_type=object_type,
        tenant_id=principal.tenant_id,
        workspace_id=core.WORKSPACE_ID,
    )


@router.get("/objects/{object_type}/{instance_id}")
async def get_generic_object(
    object_type: str,
    instance_id: str,
    as_of: Optional[datetime] = None,
    principal: Principal = Depends(core.current_principal),
) -> dict:
    definition = await _generic_object_type_or_404(object_type, principal)
    await core._authorize_object_type(principal, definition["urn"], "read")
    dataset_name = definition["source_dataset_urn"].rsplit(":", 1)[-1]
    fetch_fn = functools.partial(resolver.fetch_generic, dataset_name)
    row = await core._resolve_one(
        object_type, principal.tenant_id, instance_id, fetch_fn, "id_value", as_of=as_of, principal=principal
    )
    if row is None:
        detail = f"{object_type}/{instance_id} not found"
        if as_of is not None:
            detail += f" as of {as_of.isoformat()} (no history recorded yet at that time)"
        raise HolonError.not_found(
            "ObjectInstanceNotFound", detail, object_type=object_type, instance_id=instance_id
        )
    from ...api.object_wire import enrich_object_row

    # Historical read reports the object's own state as of that time —
    # applying *today's* Action-overlay edits to a past snapshot would mix
    # two different points in time into one answer.
    if as_of is not None:
        return enrich_object_row(
            row,
            object_type=object_type,
            tenant_id=principal.tenant_id,
            workspace_id=core.WORKSPACE_ID,
        )
    merged = (await _merge_declarative_edits([row], object_type, principal.tenant_id))[0]
    merged["title"] = ontology.title_of(merged, definition)
    return enrich_object_row(
        merged,
        object_type=object_type,
        tenant_id=principal.tenant_id,
        workspace_id=core.WORKSPACE_ID,
    )


class InvokeActionRequest(BaseModel):
    reason: str
    parameters: dict = {}
    ttl_seconds: Optional[int] = None


class PreviewActionRequest(BaseModel):
    parameters: dict = {}


class BatchInvokeActionRequest(BaseModel):
    reason: str
    instance_ids: list[str]
    parameters: dict = {}
    ttl_seconds: Optional[int] = None


async def _resolve_action_for_object(
    *,
    object_type: str,
    action_name: str,
    principal: Principal,
    workspace_id: str,
) -> tuple[str, dict, str]:
    try:
        object_type_urn = await core._object_type_urn_for(
            object_type, tenant_id=principal.tenant_id, workspace_id=workspace_id
        )
    except KeyError:
        raise HolonError.not_found("ObjectTypeNotFound", f"unknown ObjectType: {object_type}", object_type=object_type)
    qualified_name = action_name if "." in action_name else f"{object_type}.{action_name}"
    action_type = await ontology.get_action_type(core.pool, principal.tenant_id, qualified_name)
    if action_type is None and qualified_name != action_name:
        action_type = await ontology.get_action_type(core.pool, principal.tenant_id, action_name)
        qualified_name = action_name
    if action_type is None:
        raise HolonError.not_found("ActionTypeNotFound", f"unknown Action Type: {action_name}", action_name=action_name)
    await core._authorize_object_type(principal, object_type_urn, action_type["required_permission"])
    return qualified_name, action_type, object_type_urn


@router.post("/objects/{object_type}/{instance_id}/actions/{action_name}/preview")
async def preview_generic_action(
    object_type: str,
    instance_id: str,
    action_name: str,
    request: PreviewActionRequest,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """Dry-run validation for an instance-scoped Action (no writes)."""
    qualified_name, _action_type, _urn = await _resolve_action_for_object(
        object_type=object_type,
        action_name=action_name,
        principal=principal,
        workspace_id=workspace_id,
    )
    try:
        report = await validate_generic_action(
            core.pool,
            action_name=qualified_name,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            object_type=object_type,
            instance_id=instance_id,
            principal=principal,
            parameters=request.parameters,
        )
    except LookupError as exc:
        raise HolonError.not_found("ActionNotFound", str(exc)) from exc
    body = validation_report(report)
    body["target"] = {"objectType": object_type, "primaryKey": str(instance_id)}
    return body


@router.post("/objects/{object_type}/{instance_id}/actions/{action_name}")
async def invoke_generic_action(
    object_type: str, instance_id: str, action_name: str, request: InvokeActionRequest,
    principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """The one invocation endpoint for every declarative Action Type.
    `action_name` accepts either the full `ObjectType.actionName` form or,
    as a convenience, a bare local name — resolved by qualifying it with
    this route's own `object_type` first (matching how Intelligence's
    agent runtime and generated OSDK clients call well-known Actions by
    local name). Write-tier gated the same way every mutation on an
    ObjectType already is; parameter format and submission-criteria
    validation happen inside `actions.request_generic_action` before
    anything is requested or applied, so a bad call never reaches a 500.
    """
    qualified_name, _action_type, _urn = await _resolve_action_for_object(
        object_type=object_type,
        action_name=action_name,
        principal=principal,
        workspace_id=workspace_id,
    )
    try:
        apply_result = await request_generic_action(
            core.pool,
            action_name=qualified_name,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            object_type=object_type,
            instance_id=instance_id,
            principal=principal,
            reason=request.reason,
            parameters=request.parameters,
            ttl_seconds=request.ttl_seconds,
        )
    except ActionValidationError as exc:
        raise exc.to_holon_error() from exc
    except LookupError as exc:
        raise HolonError.not_found('ActionNotFound', str(exc)) from exc
    except ValueError as exc:
        raise HolonError.invalid_argument("InvalidArgument", str(exc)) from exc
    return success_envelope(
        tenant_id=principal.tenant_id,
        workspace_id=workspace_id,
        object_type=object_type,
        primary_key=instance_id,
        apply_result=apply_result,
    )


@router.post("/objects/{object_type}/actions/{action_name}/batch")
async def invoke_action_batch(
    object_type: str,
    action_name: str,
    request: BatchInvokeActionRequest,
    principal: Principal = Depends(core.current_principal),
    workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """Bounded sequential batch invoke of a declarative Action Type (P2d)."""
    if not request.instance_ids:
        raise HolonError.invalid_argument("EmptyBatch", "instance_ids must be non-empty")
    if len(request.instance_ids) > 50:
        raise HolonError.invalid_argument("BatchTooLarge", "instance_ids capped at 50 per batch", limit=50)
    qualified_name, _action_type, _urn = await _resolve_action_for_object(
        object_type=object_type,
        action_name=action_name,
        principal=principal,
        workspace_id=workspace_id,
    )

    results: list[dict] = []
    for instance_id in request.instance_ids:
        try:
            outcome = await request_generic_action(
                core.pool,
                action_name=qualified_name,
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                object_type=object_type,
                instance_id=str(instance_id),
                principal=principal,
                reason=request.reason,
                parameters=request.parameters,
                ttl_seconds=request.ttl_seconds,
            )
            results.append(
                {
                    "ok": True,
                    "result": success_envelope(
                        tenant_id=principal.tenant_id,
                        workspace_id=workspace_id,
                        object_type=object_type,
                        primary_key=str(instance_id),
                        apply_result=outcome,
                    ),
                }
            )
        except ActionValidationError as exc:
            results.append(
                {
                    "ok": False,
                    "target": {"objectType": object_type, "primaryKey": str(instance_id)},
                    "error": exc.to_holon_error().to_body(service="knowledge-platform"),
                }
            )
        except LookupError as exc:
            results.append(
                {
                    "ok": False,
                    "target": {"objectType": object_type, "primaryKey": str(instance_id)},
                    "error": HolonError.not_found("ActionNotFound", str(exc)).to_body(service="knowledge-platform"),
                }
            )
        except ValueError as exc:
            results.append(
                {
                    "ok": False,
                    "target": {"objectType": object_type, "primaryKey": str(instance_id)},
                    "error": HolonError.invalid_argument("InvalidArgument", str(exc)).to_body(
                        service="knowledge-platform"
                    ),
                }
            )
        except Exception:
            # Isolate the failure to this instance — don't abort the batch.
            logger.exception("batch action %s failed for instance %s", action_name, instance_id)
            results.append(
                {
                    "ok": False,
                    "target": {"objectType": object_type, "primaryKey": str(instance_id)},
                    "error": HolonError.internal("InternalError", "internal error").to_body(
                        service="knowledge-platform"
                    ),
                }
            )
    return {
        "action": qualified_name,
        "objectType": object_type,
        "count": len(results),
        "succeeded": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "results": results,
    }


@router.post("/action-invocations/{invocation_id}/revert")
async def revert_action_invocation(
    invocation_id: int, principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace)
) -> dict:
    """The backing endpoint for a Timeline "Undo" button — a
    user-initiated single-invocation revert, not a saga compensator (see
    `revert_declarative_action`'s own docstring for that distinction).
    Loads the invocation first since its ObjectType (and so its
    permission tier) isn't in the URL — `core._object_type_urn_for` is
    used rather than `_generic_object_type_or_404` because a declarative
    Action can target *any* ObjectType, seeded or self-serve, unlike
    every other route in this file which only ever serves the self-serve
    half.
    """
    row = await core.pool.fetchrow(
        "SELECT action_name, instance_urn FROM action_invocation WHERE id = $1 AND tenant_id = $2",
        invocation_id, principal.tenant_id,
    )
    if row is None:
        raise HolonError.not_found("ActionInvocationNotFound", f"action invocation {invocation_id} not found", invocation_id=invocation_id)
    object_type, _ = _object_type_and_instance_id_from_instance_urn(row["instance_urn"])
    try:
        object_type_urn = await core._object_type_urn_for(object_type, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HolonError.not_found("ObjectTypeNotFound", f"unknown ObjectType: {object_type}", object_type=object_type)
    action_type = await ontology.get_action_type(core.pool, principal.tenant_id, row["action_name"])
    required_permission = action_type["required_permission"] if action_type else "write"
    await core._authorize_object_type(principal, object_type_urn, required_permission)
    try:
        return await revert_declarative_action(
            core.pool, invocation_id=invocation_id, tenant_id=principal.tenant_id,
            workspace_id=workspace_id, actor=principal,
        )
    except LookupError as exc:
        raise HolonError.not_found('ActionNotFound', str(exc)) from exc
    except ValueError as exc:
        raise HolonError.invalid_argument("InvalidArgument", str(exc)) from exc
