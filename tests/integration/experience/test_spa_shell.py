"""SPA shell: deep links get index.html; missing hashed chunks must 404."""

from __future__ import annotations

import re
import urllib.error
import urllib.request

from conftest import EXPERIENCE


def _get(path: str) -> tuple[int, bytes, dict]:
    request = urllib.request.Request(f"{EXPERIENCE}{path}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def test_spa_deep_link_serves_index_without_caching() -> None:
    status, body, headers = _get("/applications/stale-chunk-probe")
    assert status == 200, body[:200]
    assert b'<div id="root">' in body
    cache = headers.get("Cache-Control") or headers.get("cache-control") or ""
    assert "no-cache" in cache


def test_missing_hashed_chunk_is_404_not_html() -> None:
    status, body, _ = _get("/assets/ApplicationPage-qAzUEk4I.js")
    assert status == 404, body[:200]
    assert b'<div id="root">' not in body


def test_built_hashed_asset_is_immutable() -> None:
    status, html, _ = _get("/")
    assert status == 200
    srcs = re.findall(rb'src="(/assets/[^"]+\.js)"', html)
    assert srcs, html[:300]
    status, body, headers = _get(srcs[0].decode())
    assert status == 200, body[:80]
    cache = headers.get("Cache-Control") or headers.get("cache-control") or ""
    assert "immutable" in cache
