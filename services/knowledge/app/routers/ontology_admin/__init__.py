"""Ontology governance surface — ObjectType CRUD/versions/branches,
interfaces, markings, relation-types, plus the read-only glossary/
query-log/actions catalogs that share the same "metadata, auth-only"
shape. Everything here is a governance action or a definition read, not
an instance read — the one instance-shaped exception,
`POST /objects/{type}/{id}/markings`, is grouped here rather than in
`routers/objects.py` because it's a markings/governance write, not an
object read.

Split across domain modules; `include_router` order matches the former
monolith so literal segments (`/ontology/health-check`) still win over
`/ontology/{name}`.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    action_types,
    catalog as catalog_routes,
    groups,
    interfaces,
    markings,
    object_types,
    ontology as ontology_routes,
    ops,
    relation_types,
    resource_branches,
    shared_property_types,
    value_types,
)

router = APIRouter()
router.include_router(catalog_routes.router)
router.include_router(ontology_routes.router)
router.include_router(object_types.router)
router.include_router(value_types.router)
router.include_router(shared_property_types.router)
router.include_router(action_types.router)
router.include_router(interfaces.router)
router.include_router(markings.router)
router.include_router(resource_branches.router)
router.include_router(ops.router)
router.include_router(relation_types.router)
router.include_router(groups.router)
