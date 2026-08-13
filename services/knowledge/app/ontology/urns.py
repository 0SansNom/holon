"""Pure URN builders — no I/O, no DB, imported by every other module in
this package (and by `core.py`/routers outside it). Kept separate so
nothing here can accidentally grow a dependency on anything that isn't
itself equally dependency-free.
"""

from __future__ import annotations

from holon_common import build_urn


def object_type_urn(tenant_id: str, workspace_id: str, name: str) -> str:
    return build_urn(tenant_id, workspace_id, "object-type", name)


def relation_type_urn(tenant_id: str, workspace_id: str, name: str) -> str:
    return build_urn(tenant_id, workspace_id, "relation-type", name)


def workspace_urn(tenant_id: str, workspace_id: str) -> str:
    return build_urn(tenant_id, "global", "workspace", workspace_id)
