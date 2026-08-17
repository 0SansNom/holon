"""Reject requests outside the public `/api/…` Knowledge surface."""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Receive, Scope, Send

_ALLOW_EXACT = frozenset({"/health", "/live", "/ready", "/metrics", "/docs", "/openapi.json", "/redoc"})
_ALLOW_PREFIX = (
    "/api/",
    "/docs/",
    "/redoc/",
)


def _allowed(path: str) -> bool:
    if path in _ALLOW_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in _ALLOW_PREFIX)


class PublicApiOnlyMiddleware:
    """404 paths that are not part of the public Knowledge API."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path") or ""
            if not _allowed(path):
                body = json.dumps(
                    {
                        "detail": (
                            f"unknown path {path!r}; use /api/ontologies/{{ontology}}/… "
                            "or /api/holon/… (see docs/api/overview.md)"
                        ),
                        "errorCode": "NOT_FOUND",
                        "errorName": "PathNotFound",
                        "errorInstanceId": "00000000-0000-0000-0000-000000000000",
                        "parameters": {"path": path},
                        "service": "knowledge-platform",
                    }
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 404,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return

        await self.app(scope, receive, send)
