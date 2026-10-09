"""Experience HTTP routers. The SPA catch-all is not included here.

`main.py` includes that router after every API route so `GET /{full_path:path}`
cannot swallow them.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import applications, collections, plugins, proxy, resources

router = APIRouter()
router.include_router(proxy.router)
router.include_router(applications.router)
router.include_router(resources.router)
router.include_router(collections.router)
router.include_router(plugins.router)
