"""Object instance wire helpers (`__apiName`, `__primaryKey`, `__rid`)."""

from __future__ import annotations

from typing import Any

from holon_common import build_urn


def enrich_object_row(
    row: dict,
    *,
    object_type: str,
    tenant_id: str,
    workspace_id: str,
) -> dict:
    out = dict(row)
    pk: Any = out.get("id")
    out.setdefault("__apiName", object_type)
    out.setdefault("__primaryKey", pk)
    if "__rid" not in out and pk is not None:
        out["__rid"] = build_urn(tenant_id, workspace_id, "object", f"{object_type}/{pk}")
    return out


def enrich_object_rows(
    rows: list[dict],
    *,
    object_type: str,
    tenant_id: str,
    workspace_id: str,
) -> list[dict]:
    return [
        enrich_object_row(r, object_type=object_type, tenant_id=tenant_id, workspace_id=workspace_id)
        for r in rows
    ]


def enrich_page(
    page: dict,
    *,
    object_type: str,
    tenant_id: str,
    workspace_id: str,
) -> dict:
    items = page.get("data") or []
    enriched = enrich_object_rows(
        items, object_type=object_type, tenant_id=tenant_id, workspace_id=workspace_id
    )
    page["data"] = enriched
    return page
