"""Mutable catalog runtime status (join backfill + search reindex)."""
from __future__ import annotations

import asyncio

join_link_backfill_status = "idle"
search_reindex_status: dict = {"state": "idle"}
search_skipped_invalid: dict[str, int] = {}
_SEARCH_REINDEX_RETRY_SECONDS = (30.0, 60.0, 120.0, 300.0)
_ENSURE_MISS_TTL_SECONDS = 30.0
_ensure_locks: dict[str, asyncio.Lock] = {}
_ensure_miss_until: dict[str, float] = {}
