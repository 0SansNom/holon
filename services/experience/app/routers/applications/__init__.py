"""Application builder routes.

Subrouters are included in the original registration order so the OpenAPI
document lists drafts, data, dashboard, analytics, forms, then agents.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import agents, analytics, catalog, dashboard, data, forms

router = APIRouter()
router.include_router(catalog.router)
router.include_router(data.router)
router.include_router(dashboard.router)
router.include_router(analytics.router)
router.include_router(forms.router)
router.include_router(agents.router)
