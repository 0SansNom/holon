"""Automation HTTP routers — audit and workflows."""

from __future__ import annotations

from fastapi import APIRouter

from . import audit, workflows

router = APIRouter()
router.include_router(audit.router)
router.include_router(workflows.router)
