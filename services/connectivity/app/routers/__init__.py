"""Connectivity HTTP routers."""
from __future__ import annotations

from fastapi import APIRouter

from . import admin, pipelines, plugins, sources, streams, sync, writebacks

router = APIRouter()
router.include_router(admin.router)
router.include_router(sync.router)
router.include_router(pipelines.router)
router.include_router(streams.router)
router.include_router(plugins.router)
router.include_router(sources.router)
router.include_router(writebacks.router)
