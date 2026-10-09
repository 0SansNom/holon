"""Connectivity sources routes — one router per connector kind."""
from __future__ import annotations

from fastapi import APIRouter

from . import generic, object_store, salesforce, sftp, sql
from ._shared import (  # noqa: F401
    _authorize_source_update,
    _delete_registered_source,
    _set_source_status,
)

router = APIRouter()
router.include_router(generic.router)
router.include_router(sql.router)
router.include_router(object_store.router)
router.include_router(sftp.router)
router.include_router(salesforce.router)
