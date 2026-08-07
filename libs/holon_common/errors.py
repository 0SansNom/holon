"""Shared error-response contract.

No service registered a global exception handler before this — an
unhandled exception anywhere fell through to FastAPI's own default,
which returns a bare `Internal Server Error` (plain text, not even
JSON) with no server-side context beyond the traceback in stdout.
Without global handling, uncaught exceptions (such as `NoSuchTableError`)
cause downstream proxy helpers to crash attempting to parse non-JSON bodies.
This module ensures all unhandled exceptions across all services degrade
to a safe, valid-JSON `{"detail": "internal server error"}` 500 — logged
server-side with the traceback, never leaked to the caller.

Deliberately does not touch `HTTPException` handling — FastAPI's default
there is already correct (`{"detail": <string>}`, exactly the shape the
frontend's `ApiError` already expects) and every service's existing
`except SomeError: raise HTTPException(...)` call sites keep working
completely unchanged.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("holon_common.errors")


def install_error_handlers(app: FastAPI, *, service_name: str) -> None:
    """Call once per service, alongside the existing `instrument_cors`/
    `instrument_metrics`/`instrument_tracing` calls in each `main.py`.
    """

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled exception in %s while handling %s %s", service_name, request.method, request.url.path
        )
        return JSONResponse(status_code=500, content={"detail": "internal server error"})
