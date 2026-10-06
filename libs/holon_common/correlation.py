"""One correlation id per user action, carried across services.

An inbound request takes `X-Correlation-ID` (or gets a new one), outbound
`httpx` calls forward it, events published while handling it carry it as
`correlation_id`, and a consumer restores it while handling the event. Audit
records store it as `traceId`, so one action can be followed through every
service's audit trail.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar
from typing import Optional

import httpx

HEADER = "X-Correlation-ID"
_VALID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_current: ContextVar[Optional[str]] = ContextVar("holon_correlation_id", default=None)
_httpx_patched = False


def current_correlation_id() -> Optional[str]:
    return _current.get()


def set_correlation_id(correlation_id: Optional[str]) -> None:
    _current.set(correlation_id)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def _accept(value: Optional[str]) -> Optional[str]:
    return value if value and _VALID.match(value) else None


class CorrelationMiddleware:
    """Pure ASGI so the id is set in the same context the route runs in."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inbound = None
        for name, value in scope.get("headers") or []:
            if name.decode("latin-1").lower() == HEADER.lower():
                inbound = _accept(value.decode("latin-1"))
                break
        correlation_id = inbound or new_correlation_id()
        token = _current.set(correlation_id)

        async def send_with_header(message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((HEADER.lower().encode("latin-1"), correlation_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            _current.reset(token)


def _patch_httpx() -> None:
    global _httpx_patched
    if _httpx_patched:
        return
    original_send = httpx.AsyncClient.send

    async def send(self, request: httpx.Request, *args, **kwargs):
        correlation_id = _current.get()
        if correlation_id and HEADER not in request.headers:
            request.headers[HEADER] = correlation_id
        return await original_send(self, request, *args, **kwargs)

    httpx.AsyncClient.send = send
    _httpx_patched = True


def instrument_correlation(app) -> None:
    app.add_middleware(CorrelationMiddleware)
    _patch_httpx()
