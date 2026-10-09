"""Intelligence HTTP routers — audit, rag, sessions, plugins, models."""

from __future__ import annotations

from fastapi import APIRouter

from . import audit, models, plugins, rag, sessions

router = APIRouter()
router.include_router(audit.router)
router.include_router(rag.router)
router.include_router(sessions.router)
router.include_router(plugins.router)
router.include_router(models.router)
