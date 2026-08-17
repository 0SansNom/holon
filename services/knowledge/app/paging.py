"""Keyset cursor paging for Ontology collection reads.

Query params: `pageSize`, `pageToken` (aliases `page_size`, `cursor` accepted).

Response:

  {
    "data": [ ... ],
    "nextPageToken": "…" | null,
    "pageSize": 50
  }
"""

from __future__ import annotations

import base64
import json
from typing import Any, Callable, Optional

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
# Hard stop for a single cursor walk / omitted-page_size one-shot.
MAX_WALK_ITEMS = 10_000

_CURSOR_VERSION = 1

KeyOf = Callable[[dict], Any]


class PagingError(ValueError):
    """Invalid cursor or pageSize — map to HTTP 400."""


def clamp_page_size(page_size: Optional[int]) -> int:
    if page_size is None:
        return DEFAULT_PAGE_SIZE
    if not isinstance(page_size, int) or isinstance(page_size, bool) or page_size < 1:
        raise PagingError("pageSize must be a positive integer")
    if page_size > MAX_WALK_ITEMS:
        raise PagingError(f"pageSize must be ≤ {MAX_WALK_ITEMS}")
    return page_size


def _sort_key(instance_id: Any) -> tuple:
    text = "" if instance_id is None else str(instance_id)
    if text.isdigit():
        return (0, int(text))
    return (1, text)


def instance_id_of(row: dict) -> Any:
    if "id" in row and row["id"] is not None:
        return row["id"]
    raise PagingError("row missing id — cannot page")


def interface_instance_key(row: dict) -> str:
    """Stable unique key across polymorphic interface collections."""
    ot = row.get("_objectType") or ""
    return f"{ot}:{instance_id_of(row)}"


def encode_cursor(*, after_id: Any) -> str:
    payload = {"v": _CURSOR_VERSION, "after_id": after_id}
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> Any:
    if not cursor or not isinstance(cursor, str):
        raise PagingError("pageToken must be a non-empty string")
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode())
        payload = json.loads(raw.decode())
    except (ValueError, json.JSONDecodeError) as exc:
        raise PagingError("invalid pageToken") from exc
    if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION or "after_id" not in payload:
        raise PagingError("invalid pageToken")
    return payload["after_id"]


def paginate_rows(
    rows: list[dict],
    *,
    page_size: Optional[int] = None,
    cursor: Optional[str] = None,
    key_of: KeyOf = instance_id_of,
) -> dict:
    """Slice `rows` into a page envelope. Does not mutate input order of
    the source beyond sorting a shallow copy by `key_of`.
    """
    size = clamp_page_size(page_size)
    ordered = sorted(rows, key=lambda r: _sort_key(key_of(r)))
    start = 0
    if cursor is not None:
        after_id = decode_cursor(cursor)
        after_key = _sort_key(after_id)
        for index, row in enumerate(ordered):
            if _sort_key(key_of(row)) > after_key:
                start = index
                break
        else:
            start = len(ordered)

    page = ordered[start : start + size]
    next_token = None
    if start + size < len(ordered) and page:
        next_token = encode_cursor(after_id=key_of(page[-1]))
    reported = size if size <= MAX_PAGE_SIZE else DEFAULT_PAGE_SIZE
    return {
        "data": page,
        "nextPageToken": next_token,
        "pageSize": reported,
    }
