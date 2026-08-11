"""Generic self-serve reads — `ontology.create_object_type`'s counterpart.
Registered last, on purpose: `objects/__init__.py` combines this
router's routes after `seeded.py`'s, per that file's own module
docstring (Starlette matches by registration order) — these two routes
are the most general shape in the package (a bare `/objects/{object_type}`
and `/objects/{object_type}/{instance_id}`), and would shadow every
specific route in `seeded.py` if registered any earlier, including
`/objects/Customer` itself. They only ever get reached for a name that
isn't one of the six boot-known types, at which point
`core._resolve_one`/`_resolve_many`'s own dynamic-URN fallback and
`resolver.fetch_generic` take over. No Customer-specific overlay
(`seeded._merge_action_overlays`) applies here — that one really is
specific to Customer — but `_merge_declarative_edits` below *does*, the
generic counterpart every self-serve ObjectType gets automatically.
"""

from __future__ import annotations

import functools
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from holon_common import Principal

from ... import ontology, resolver
from ... import core
from ...actions import request_generic_action, revert_declarative_action
from ...actions.declarative import _object_type_and_instance_id_from_instance_urn

router = APIRouter()


async def _generic_object_type_or_404(object_type: str, principal: Principal) -> dict:
    if object_type in core.OBJECT_TYPE_URNS and principal.tenant_id == core.TENANT_ID:
        # Already served by a specific route in `seeded.py`; reaching
        # here would mean registration order broke, not that this name
        # is unknown. (Other tenants never hit the seeded routes' ReBAC
        # on bootstrap URNs — they resolve via catalogue below.)
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {object_type}")
    types = await ontology.list_object_types(core.pool, principal.tenant_id)
    for definition in types:
        if definition.get("name") == object_type or definition["urn"].rsplit(":", 1)[-1] == object_type:
            return definition
    raise HTTPException(status_code=404, detail=f"unknown ObjectType: {object_type}")


async def _merge_declarative_edits(rows: list[dict], object_type: str, tenant_id: str) -> list[dict]:
    """The generic read half of the `actions` package's
    `object_instance_edit` overlay — a declarative Action Type's applied
    edits become visible on the very next read, the same "no new overlay
    function per Action" property `seeded._merge_action_overlays` never
    had (it's one bespoke function per Customer Action; this one is the
    same function for every ObjectType and every declarative Action
    Type, forever).
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
async def list_generic_objects(object_type: str, principal: Principal = Depends(core.current_principal)) -> list[dict]:
    definition = await _generic_object_type_or_404(object_type, principal)
    await core._authorize_object_type(principal, definition["urn"], "read")
    dataset_name = definition["source_dataset_urn"].rsplit(":", 1)[-1]
    fetch_fn = functools.partial(resolver.fetch_generic, dataset_name)
    rows = await core._resolve_many(object_type, principal.tenant_id, fetch_fn, principal=principal)
    rows = await _merge_declarative_edits(rows, object_type, principal.tenant_id)
    for row in rows:
        row["title"] = ontology.title_of(row, definition)
    return rows


@router.get("/objects/{object_type}/{instance_id}")
async def get_generic_object(
    object_type: str, instance_id: str, principal: Principal = Depends(core.current_principal)
) -> dict:
    definition = await _generic_object_type_or_404(object_type, principal)
    await core._authorize_object_type(principal, definition["urn"], "read")
    dataset_name = definition["source_dataset_urn"].rsplit(":", 1)[-1]
    fetch_fn = functools.partial(resolver.fetch_generic, dataset_name)
    row = await core._resolve_one(object_type, principal.tenant_id, instance_id, fetch_fn, "id_value", principal=principal)
    if row is None:
        raise HTTPException(status_code=404, detail=f"{object_type}/{instance_id} not found")
    merged = (await _merge_declarative_edits([row], object_type, principal.tenant_id))[0]
    merged["title"] = ontology.title_of(merged, definition)
    return merged


class InvokeActionRequest(BaseModel):
    reason: str
    parameters: dict = {}
    ttl_seconds: Optional[int] = None


@router.post("/objects/{object_type}/{instance_id}/actions/{action_name}")
async def invoke_generic_action(
    object_type: str, instance_id: str, action_name: str, request: InvokeActionRequest,
    principal: Principal = Depends(core.current_principal), workspace_id: str = Depends(core.current_workspace),
) -> dict:
    """The generic invocation endpoint for a declarative Action Type —
    alongside the two hardcoded Customer action endpoints in
    `routers/actions.py` (unchanged), not a replacement for them.
    Write-tier gated the same way every mutation on an ObjectType
    already is; parameter format and submission-criteria validation
    happen inside `actions.request_generic_action` before anything is
    requested or applied, so a bad call never reaches a 500.
    """
    # Deliberately not `_generic_object_type_or_404` — that helper exists
    # to keep this router's own generic list/get routes from shadowing
    # `seeded.py`'s specific ones, and rejects every seeded type name for
    # that reason. Action invocation isn't split seeded-vs-generic like
    # reads are (this is the *only* route for any declarative Action,
    # confirmed via `routers/objects/__init__.py`'s own docstring — the
    # two hardcoded Customer actions are the sole exception, via their
    # own literal routes in `routers/actions.py`), so a declarative
    # Action Type targeting a seeded ObjectType was unreachable here
    # before this fix — `core._object_type_urn_for` is the
    # seeded-or-self-serve-agnostic resolver every other cross-cutting
    # route (e.g. the revert endpoint below) already uses instead.
    try:
        object_type_urn = await core._object_type_urn_for(object_type, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {object_type}")
    action_type = await ontology.get_action_type(core.pool, principal.tenant_id, action_name)
    if action_type is None:
        raise HTTPException(status_code=404, detail=f"unknown Action Type: {action_name}")
    await core._authorize_object_type(principal, object_type_urn, action_type["required_permission"])
    try:
        return await request_generic_action(
            core.pool,
            action_name=action_name,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            object_type=object_type,
            instance_id=instance_id,
            principal=principal,
            reason=request.reason,
            parameters=request.parameters,
            ttl_seconds=request.ttl_seconds,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
        raise HTTPException(status_code=404, detail=f"action invocation {invocation_id} not found")
    object_type, _ = _object_type_and_instance_id_from_instance_urn(row["instance_urn"])
    try:
        object_type_urn = await core._object_type_urn_for(object_type, tenant_id=principal.tenant_id, workspace_id=workspace_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ObjectType: {object_type}")
    action_type = await ontology.get_action_type(core.pool, principal.tenant_id, row["action_name"])
    required_permission = action_type["required_permission"] if action_type else "write"
    await core._authorize_object_type(principal, object_type_urn, required_permission)
    try:
        return await revert_declarative_action(
            core.pool, invocation_id=invocation_id, tenant_id=principal.tenant_id,
            workspace_id=workspace_id, actor=principal,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
