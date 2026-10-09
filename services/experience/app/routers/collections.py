"""Workspace resource collections."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from holon_common import HolonError, Principal

from .. import collections as resource_collections
from .. import deps
from ..deps import (
    _authorize_resource,
    _authorize_workspace,
    _filter_readable_resource_urns,
    _resource_authz_type,
    current_principal,
)

router = APIRouter()


def _collection_not_found(collection_id: int) -> HolonError:
    return HolonError.not_found(
        "CollectionNotFound", f"no collection with id {collection_id}", collection_id=collection_id
    )


class CreateCollectionRequest(BaseModel):
    name: str
    description: str = ""


@router.post("/api/collections")
async def create_collection(body: CreateCollectionRequest, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    if await resource_collections.get_collection_by_name(deps.pool, tenant_id=principal.tenant_id, name=body.name):
        raise HolonError.conflict('CollectionAlreadyExists', f"a collection named {body.name!r} already exists")
    return await resource_collections.create_collection(
        deps.pool, tenant_id=principal.tenant_id, name=body.name, description=body.description,
        created_by_urn=principal.urn,
    )


@router.get("/api/collections")
async def list_collections(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await resource_collections.list_collections(deps.pool, tenant_id=principal.tenant_id)


@router.get("/api/collections/{collection_id}")
async def get_collection(collection_id: int, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "read")
    collection = await resource_collections.get_collection(deps.pool, tenant_id=principal.tenant_id, collection_id=collection_id)
    if collection is None:
        raise _collection_not_found(collection_id)
    members = await resource_collections.list_members(
        deps.pool, tenant_id=principal.tenant_id, collection_id=collection_id,
    )
    collection["members"] = await _filter_readable_resource_urns(principal, members)
    return collection


@router.delete("/api/collections/{collection_id}")
async def delete_collection(collection_id: int, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    await resource_collections.delete_collection(deps.pool, tenant_id=principal.tenant_id, collection_id=collection_id)
    return {"status": "deleted", "id": collection_id}


class SetCollectionMembersRequest(BaseModel):
    resource_urns: list[str]


@router.put("/api/collections/{collection_id}/members")
async def set_collection_members(
    collection_id: int, body: SetCollectionMembersRequest, principal: Principal = Depends(current_principal),
) -> dict:
    await _authorize_workspace(principal, "write")
    if await resource_collections.get_collection(deps.pool, tenant_id=principal.tenant_id, collection_id=collection_id) is None:
        raise _collection_not_found(collection_id)
    members = await resource_collections.set_members(
        deps.pool, tenant_id=principal.tenant_id, collection_id=collection_id,
        resource_urns=body.resource_urns, added_by_urn=principal.urn,
    )
    return {"id": collection_id, "members": members}


@router.post("/api/collections/{collection_id}/members/{resource_urn}")
async def add_collection_member(
    collection_id: int, resource_urn: str, principal: Principal = Depends(current_principal),
) -> dict:
    await _authorize_workspace(principal, "write")
    if await resource_collections.get_collection(deps.pool, tenant_id=principal.tenant_id, collection_id=collection_id) is None:
        raise _collection_not_found(collection_id)
    await resource_collections.add_member(
        deps.pool, tenant_id=principal.tenant_id, collection_id=collection_id, resource_urn=resource_urn,
        added_by_urn=principal.urn,
    )
    return {"status": "added"}


@router.delete("/api/collections/{collection_id}/members/{resource_urn}")
async def remove_collection_member(
    collection_id: int, resource_urn: str, principal: Principal = Depends(current_principal),
) -> dict:
    await _authorize_workspace(principal, "write")
    if await resource_collections.get_collection(deps.pool, tenant_id=principal.tenant_id, collection_id=collection_id) is None:
        raise _collection_not_found(collection_id)
    await resource_collections.remove_member(
        deps.pool, tenant_id=principal.tenant_id, collection_id=collection_id, resource_urn=resource_urn,
    )
    return {"status": "removed"}


@router.get("/api/resources/{urn}/collections")
async def list_resource_collections(urn: str, principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    await _authorize_resource(principal, _resource_authz_type(urn), urn, "read")
    return await resource_collections.list_collections_for_resource(deps.pool, tenant_id=principal.tenant_id, resource_urn=urn)
