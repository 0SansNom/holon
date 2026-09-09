"""Identity HTTP routers — auth, federation, directory, access."""
from __future__ import annotations

from fastapi import APIRouter

from . import access, auth, directory, federation

router = APIRouter()
router.include_router(auth.router)
router.include_router(federation.router)
router.include_router(directory.router)
router.include_router(access.router)
