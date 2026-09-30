from __future__ import annotations

from fastapi import APIRouter

from . import applications, collections, plugins, proxy, resources, spa

router = APIRouter()
router.include_router(proxy.router)
router.include_router(applications.router)
router.include_router(resources.router)
router.include_router(collections.router)
router.include_router(plugins.router)
# Catch-all. Must stay last or it swallows the API routes above.
router.include_router(spa.router)
