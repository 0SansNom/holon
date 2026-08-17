"""Rewrite `/api/ontologies|holon/...` onto internal Knowledge route handlers."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qsl, urlencode

from starlette.types import ASGIApp, Receive, Scope, Send

_ONTOLOGY = re.compile(r"^/api/ontologies/([^/]+)(/.*)?$")
_HOLON = re.compile(r"^/api/holon(/.*)?$")

_SUFFIX_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^/objectTypes$"), "/ontology"),
    (re.compile(r"^/objectTypes/(.*)"), r"/ontology/\1"),
    (re.compile(r"^/linkTypes(.*)$"), r"/relation-types\1"),
    (re.compile(r"^/interfaceTypes(.*)$"), r"/interfaces\1"),
    (re.compile(r"^/sharedPropertyTypes(.*)$"), r"/shared-property-types\1"),
    (re.compile(r"^/valueTypes(.*)$"), r"/value-types\1"),
    (re.compile(r"^/actionTypes(.*)$"), r"/action-types\1"),
    (re.compile(r"^/objectSets(.*)$"), r"/object-sets\1"),
    (re.compile(r"^/objects(/.*)?$"), r"/objects\1"),
]


def _rewrite_suffix(suffix: str) -> Optional[str]:
    if not suffix or suffix == "/":
        return None
    for pattern, repl in _SUFFIX_RULES:
        if pattern.match(suffix):
            return pattern.sub(repl, suffix)
    return None


def _alias_paging_query(query: bytes) -> bytes:
    """Map pageSize/pageToken → page_size/cursor without dropping others."""
    if not query:
        return query
    pairs = parse_qsl(query.decode(), keep_blank_values=True)
    out: list[tuple[str, str]] = []
    for key, value in pairs:
        if key == "pageSize":
            if not any(k == "page_size" for k, _ in pairs):
                out.append(("page_size", value))
            continue
        if key == "pageToken":
            if not any(k == "cursor" for k, _ in pairs):
                out.append(("cursor", value))
            continue
        out.append((key, value))
    return urlencode(out).encode()


class ApiPathRewriteMiddleware:
    """Rewrite public `/api/...` paths onto internal routers."""

    def __init__(self, app: ASGIApp, *, default_ontology: str = "main") -> None:
        self.app = app
        self.default_ontology = default_ontology

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""
        ontology_match = _ONTOLOGY.match(path)
        if ontology_match:
            ontology = ontology_match.group(1)
            suffix = ontology_match.group(2) or "/"
            scope.setdefault("state", {})
            if isinstance(scope["state"], dict):
                scope["state"]["api_ontology"] = ontology
            rewritten = _rewrite_suffix(suffix)
            if rewritten is not None:
                scope = dict(scope)
                scope["path"] = rewritten
                scope["raw_path"] = rewritten.encode()
                qs = scope.get("query_string") or b""
                scope["query_string"] = _alias_paging_query(qs)
                await self.app(scope, receive, send)
                return

        holon_match = _HOLON.match(path)
        if holon_match:
            rest = holon_match.group(1) or ""
            if not rest or rest == "/":
                await self.app(scope, receive, send)
                return
            scope = dict(scope)
            scope["path"] = rest
            scope["raw_path"] = rest.encode()
            qs = scope.get("query_string") or b""
            scope["query_string"] = _alias_paging_query(qs)
            await self.app(scope, receive, send)
            return

        await self.app(scope, receive, send)
