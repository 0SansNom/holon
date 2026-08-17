"""Shared FastAPI helpers for collection paging query params."""

from __future__ import annotations

from typing import Optional

from fastapi import Query
from holon_common import HolonError

from ... import paging


def paging_query(
    page_size: Optional[int] = Query(None, ge=1, le=paging.MAX_PAGE_SIZE),
    pageSize: Optional[int] = Query(None, ge=1, le=paging.MAX_PAGE_SIZE),
    cursor: Optional[str] = Query(None),
    pageToken: Optional[str] = Query(None),
) -> tuple[int, Optional[str]]:
    size = page_size if page_size is not None else pageSize
    token = cursor if cursor is not None else pageToken
    if size is None:
        return paging.MAX_WALK_ITEMS, token
    return size, token


def page_response(
    rows: list[dict],
    *,
    page_size: int,
    cursor: Optional[str],
    key_of=None,
) -> dict:
    """In-memory page (Iceberg fallback / polymorphic mixes). Prefer
    `page_from_resolved` when rows were already keyset-fetched from the
    serving store with `limit=page_size+1`.
    """
    try:
        kwargs = {"page_size": page_size, "cursor": cursor}
        if key_of is not None:
            kwargs["key_of"] = key_of
        return paging.paginate_rows(rows, **kwargs)
    except paging.PagingError as exc:
        raise HolonError.invalid_argument('InvalidPageCursor', str(exc)) from exc


def page_from_resolved(rows: list[dict], *, page_size: int, key_of=None) -> dict:
    """Build a page envelope from a serving-store fetch of `page_size+1` rows."""
    key_fn = key_of or paging.instance_id_of
    has_more = len(rows) > page_size
    items = rows[:page_size]
    next_token = None
    if has_more and items:
        next_token = paging.encode_cursor(after_id=key_fn(items[-1]))
    reported = page_size if page_size <= paging.MAX_PAGE_SIZE else paging.DEFAULT_PAGE_SIZE
    return {
        "data": items,
        "nextPageToken": next_token,
        "pageSize": reported,
    }


def after_id_from_cursor(cursor: Optional[str]) -> Optional[str]:
    if cursor is None:
        return None
    try:
        return str(paging.decode_cursor(cursor))
    except paging.PagingError as exc:
        raise HolonError.invalid_argument('InvalidPageCursor', str(exc)) from exc
