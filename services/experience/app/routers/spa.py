"""SPA shell and static assets.

Included from `main.py` after every API route. Unknown paths fall back to
index.html. A missing hashed asset is a 404, never HTML, so a stale import
does not execute the shell as a module.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..deps import STATIC_DIR

router = APIRouter()

# Hashed Vite chunks under /assets/ can be cached forever. index.html and
# unhashed files must revalidate — a cached shell after `npm run build`
# points at deleted filenames.
_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
_HTML_CACHE_CONTROL = "no-cache, must-revalidate"
_STATIC_ASSET_SUFFIXES = {
    ".css",
    ".eot",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".map",
    ".png",
    ".svg",
    ".ttf",
    ".webp",
    ".woff",
    ".woff2",
}


def _looks_like_static_asset(full_path: str) -> bool:
    if full_path.startswith("assets/"):
        return True
    return Path(full_path).suffix.lower() in _STATIC_ASSET_SUFFIXES


@router.get("/{full_path:path}")
async def spa(full_path: str) -> FileResponse:
    index = STATIC_DIR / "index.html"
    candidate = (STATIC_DIR / full_path).resolve()
    if full_path and candidate.is_file() and STATIC_DIR.resolve() in candidate.parents:
        cache = _ASSET_CACHE_CONTROL if full_path.startswith("assets/") else _HTML_CACHE_CONTROL
        return FileResponse(candidate, headers={"Cache-Control": cache})
    if _looks_like_static_asset(full_path):
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(index, headers={"Cache-Control": _HTML_CACHE_CONTROL})
